# Circular-economy Classification Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add six classification columns to the `esg-longitudinal` tidy schema so ESG commitments carry 10R-strategy / enabler tags, metric-vs-target type, target completeness, and year-over-year target status.

**Architecture:** Extend the flat CSV schema (13→19 columns) in `snapshot.py`; add validation that only fires when a new field is non-empty (backward compatible). Extend `diff.py` with a "Target movements" section that compares `status=target` rows across snapshots. Enrich the `circular-economy-10rs.json` reference with an enabler taxonomy, guide assignment from `indicators.yaml`, and document the layer in `SKILL.md`. Migrate eval assertions from 13→19 columns.

**Tech Stack:** Python 3.13 stdlib only (csv, json, argparse, re), pytest 8.3.4. No new dependencies. Reference data is JSON + YAML; docs are Markdown.

## Global Constraints

- Python **stdlib only** in scripts — no new pip dependencies (matches existing `snapshot.py`/`diff.py`).
- New columns are **appended** to `COLS` and are **never required** — old 13-column rows must still validate and snapshot.
- `diff.py` `KEY = (entity, indicator, period)` is **unchanged** — eval-3's re-run diff against the 13-column 2025 baseline must still align.
- Canonical enabler ids (used in JSON, `snapshot.py`, `SKILL.md`, tests): `ecodesign, rnd, data_infrastructure, training, partnerships, reverse_logistics, finance, policy`.
- Canonical enums: `item_type ∈ {kpi, target, qualitative}`; `r_strategy` tokens ∈ `{R0…R9}` (pipe-separated, primary first); `target_has_kpi ∈ {yes, no}`; `target_status ∈ {on_track, achieved, delayed, changed, failed, dropped, too_early}`; `target_end_year` matches `^\d{4}$`.
- 19-column header, exact order:
  `entity, lei, domain, indicator, value, unit, period, status, source, source_url, page, quote, retrieved_at, item_type, r_strategy, enabler_topic, target_end_year, target_has_kpi, target_status`
- Work happens on branch `esg-circular-classification` (already checked out). Commit after each task.
- All paths below are relative to repo root `C:/Users/kmorg/claude-skills`.

---

## File Structure

- `esg-longitudinal/scripts/snapshot.py` — **Modify.** +6 COLS, enum constants, validation, `item_type` auto-fill.
- `esg-longitudinal/scripts/diff.py` — **Modify.** Target-movements section + helpers.
- `esg-longitudinal/circular-economy-10rs.json` — **Modify.** Add `enablers` array.
- `esg-longitudinal/references/indicators.yaml` — **Modify.** Add `r_hint` per circular indicator + header note.
- `esg-longitudinal/SKILL.md` — **Modify.** Schema block, classification sections, report template.
- `esg-longitudinal/evals/evals.json` — **Modify.** 13→19-column assertions, classification assertions, new eval-4.
- `esg-longitudinal/tests/test_snapshot.py` — **Create.** Schema/validation/auto-fill tests.
- `esg-longitudinal/tests/test_diff.py` — **Create.** Target-movements tests.
- `esg-longitudinal/tests/test_reference_data.py` — **Create.** Enabler JSON + indicators r_hint + evals-migration tests.
- `esg-longitudinal/tests/conftest.py` — **Create.** Path-import helpers for the two scripts.

---

### Task 1: Snapshot schema + classification validation

**Files:**
- Modify: `esg-longitudinal/scripts/snapshot.py`
- Create: `esg-longitudinal/tests/conftest.py`
- Test: `esg-longitudinal/tests/test_snapshot.py`

**Interfaces:**
- Produces: `snapshot.COLS` (19-element list), `snapshot.validate(row) -> list[str]`, `snapshot._autofill_item_type(row) -> None`, and enum constants `VALID_ITEM_TYPE`, `VALID_R`, `VALID_ENABLER`, `VALID_HAS_KPI`, `VALID_TARGET_STATUS`.
- Consumes: nothing.

- [ ] **Step 1: Create the test import helper**

Create `esg-longitudinal/tests/conftest.py`:

```python
"""Load the un-packaged scripts by file path so tests can import their functions."""
import importlib.util
import pathlib

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    path = _SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
```

- [ ] **Step 2: Write the failing tests**

Create `esg-longitudinal/tests/test_snapshot.py`:

```python
from conftest import _load

snapshot = _load("snapshot")

NEW_COLS = ["item_type", "r_strategy", "enabler_topic",
            "target_end_year", "target_has_kpi", "target_status"]


def _base(**over):
    row = {"entity": "Royal Philips", "domain": "circular",
           "indicator": "circular_revenue_pct", "period": "2022",
           "status": "found", "retrieved_at": "2026-07-02",
           "value": "18", "source_url": "http://x", "quote": "18% circular"}
    row.update(over)
    return row


def test_header_has_19_columns_in_order():
    assert snapshot.COLS[:13] == [
        "entity", "lei", "domain", "indicator", "value", "unit", "period",
        "status", "source", "source_url", "page", "quote", "retrieved_at"]
    assert snapshot.COLS[13:] == NEW_COLS
    assert len(snapshot.COLS) == 19


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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd esg-longitudinal && python -m pytest tests/test_snapshot.py -v`
Expected: FAIL — `AttributeError: module 'snapshot' has no attribute '_autofill_item_type'` and header/length assertions fail (COLS still 13).

- [ ] **Step 4: Implement the schema + validation changes**

In `esg-longitudinal/scripts/snapshot.py`, add `import re` to the imports, then replace the constants block (currently lines ~29-33) with:

```python
COLS = ["entity", "lei", "domain", "indicator", "value", "unit", "period", "status",
        "source", "source_url", "page", "quote", "retrieved_at",
        "item_type", "r_strategy", "enabler_topic",
        "target_end_year", "target_has_kpi", "target_status"]
REQUIRED = ["entity", "domain", "indicator", "period", "status", "retrieved_at"]
SOURCED_STATUSES = {"found", "target"}
VALID_STATUS = {"found", "not_found", "target"}

# Classification enums — validated ONLY when the field is non-empty, so old
# 13-column rows (which omit these fields) still pass. New fields are never required.
VALID_ITEM_TYPE = {"kpi", "target", "qualitative"}
VALID_R = {f"R{i}" for i in range(10)}  # R0..R9
VALID_ENABLER = {"ecodesign", "rnd", "data_infrastructure", "training",
                 "partnerships", "reverse_logistics", "finance", "policy"}
VALID_HAS_KPI = {"yes", "no"}
VALID_TARGET_STATUS = {"on_track", "achieved", "delayed", "changed",
                       "failed", "dropped", "too_early"}


def _autofill_item_type(row):
    """If item_type is blank, derive it from status (found->kpi, target->target).
    not_found is left blank — a gap has no metric/target character."""
    if str(row.get("item_type", "")).strip():
        return
    status = str(row.get("status", "")).strip()
    if status == "found":
        row["item_type"] = "kpi"
    elif status == "target":
        row["item_type"] = "target"
```

Then, inside `validate(row)`, append these checks before `return errs`:

```python
    it = str(row.get("item_type", "")).strip()
    if it and it not in VALID_ITEM_TYPE:
        errs.append(f"invalid item_type '{it}' (use kpi|target|qualitative)")

    rs = str(row.get("r_strategy", "")).strip()
    if rs:
        for tok in (t.strip() for t in rs.split("|")):
            if tok and tok not in VALID_R:
                errs.append(f"invalid r_strategy token '{tok}' (use R0..R9)")

    en = str(row.get("enabler_topic", "")).strip()
    if en and en not in VALID_ENABLER:
        errs.append(f"invalid enabler_topic '{en}' (see circular-economy-10rs.json)")

    hk = str(row.get("target_has_kpi", "")).strip()
    if hk and hk not in VALID_HAS_KPI:
        errs.append(f"invalid target_has_kpi '{hk}' (use yes|no)")

    ts = str(row.get("target_status", "")).strip()
    if ts and ts not in VALID_TARGET_STATUS:
        errs.append(f"invalid target_status '{ts}'")

    ey = str(row.get("target_end_year", "")).strip()
    if ey and not re.fullmatch(r"\d{4}", ey):
        errs.append(f"invalid target_end_year '{ey}' (use YYYY)")
```

Finally, in `main()`, call the auto-fill right after the `retrieved_at` default is set (inside the `for idx, r in enumerate(rows):` loop, before `errs = validate(r)`):

```python
        _autofill_item_type(r)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd esg-longitudinal && python -m pytest tests/test_snapshot.py -v`
Expected: PASS (10 tests).

- [ ] **Step 6: Commit**

```bash
git add esg-longitudinal/scripts/snapshot.py esg-longitudinal/tests/conftest.py esg-longitudinal/tests/test_snapshot.py
git commit -m "Add classification columns + validation to snapshot.py"
```

---

### Task 2: Target-movements diff

**Files:**
- Modify: `esg-longitudinal/scripts/diff.py`
- Test: `esg-longitudinal/tests/test_diff.py`

**Interfaces:**
- Consumes: `snapshot.COLS` (for writing valid fixture CSVs in the test).
- Produces: `diff.target_rows(snap) -> dict`, `diff.latest_found(snap) -> dict`; a "## Target movements" section in the report.

- [ ] **Step 1: Write the failing tests**

Create `esg-longitudinal/tests/test_diff.py`:

```python
import csv
import subprocess
import sys
import pathlib

from conftest import _load

diff = _load("diff")
snapshot = _load("snapshot")

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=snapshot.COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in snapshot.COLS})


def _t(**over):
    row = {"entity": "Royal Philips", "indicator": "circular_revenue_pct",
           "period": "2022", "status": "target", "value": "25",
           "target_end_year": "2025", "target_status": "on_track"}
    row.update(over)
    return row


def test_target_rows_picks_latest_period():
    snap = {("Royal Philips", "circular_revenue_pct", "2021"): _t(period="2021", value="20"),
            ("Royal Philips", "circular_revenue_pct", "2022"): _t(period="2022", value="25")}
    tr = diff.target_rows(snap)
    assert tr[("Royal Philips", "circular_revenue_pct")]["value"] == "25"


def test_changed_and_dropped_targets_render(tmp_path):
    old = tmp_path / "old.csv"
    new = tmp_path / "new.csv"
    # old: a 25%/2025 target + a takeback target that will be dropped
    _write(old, [_t(value="25", target_end_year="2025"),
                 _t(indicator="product_takeback_scope", value="all", target_end_year="2024")])
    # new: 25% target moved to 2030; takeback target gone; a brand-new target added
    _write(new, [_t(value="25", target_end_year="2030"),
                 _t(indicator="recycled_input_pct", value="50", target_end_year="2028")])
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "diff.py"), "--old", str(old), "--new", str(new)],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    body = out.stdout
    assert "Target movements" in body
    assert "Changed targets" in body and "2025" in body and "2030" in body
    assert "Dropped targets" in body and "product_takeback_scope" in body
    assert "New targets" in body and "recycled_input_pct" in body


def test_target_vs_actual_pairs(tmp_path):
    old = tmp_path / "old.csv"
    new = tmp_path / "new.csv"
    _write(old, [_t(value="25", target_end_year="2025")])
    _write(new, [_t(value="25", target_end_year="2025"),
                 {"entity": "Royal Philips", "indicator": "circular_revenue_pct",
                  "period": "2022", "status": "found", "value": "18",
                  "source_url": "http://x", "quote": "18%"}])
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "diff.py"), "--old", str(old), "--new", str(new)],
        capture_output=True, text=True)
    assert "Target vs latest actual" in out.stdout
    assert "18" in out.stdout and "25" in out.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd esg-longitudinal && python -m pytest tests/test_diff.py -v`
Expected: FAIL — `AttributeError: module 'diff' has no attribute 'target_rows'`.

- [ ] **Step 3: Implement the target-movements logic**

In `esg-longitudinal/scripts/diff.py`, add after the `num()` function:

```python
TKEY = ("entity", "indicator")


def _latest_by(snap, status):
    """Latest-period row per (entity, indicator) filtered to a status."""
    out = {}
    for r in snap.values():
        if str(r.get("status", "")).strip() != status:
            continue
        k = tuple(r.get(x, "") for x in TKEY)
        cur = out.get(k)
        if cur is None or str(r.get("period", "")) > str(cur.get("period", "")):
            out[k] = r
    return out


def target_rows(snap):
    """Latest-period status=target row per (entity, indicator)."""
    return _latest_by(snap, "target")


def latest_found(snap):
    """Latest-period status=found actual per (entity, indicator)."""
    return _latest_by(snap, "found")
```

Then, in `main()`, after the existing `if dropped:` block and before the `if not (added or changed or dropped):` guard, insert:

```python
    old_t, new_t = target_rows(old), target_rows(new)
    new_targets = [k for k in new_t if k not in old_t]
    dropped_targets = [k for k in old_t if k not in new_t]
    changed_targets = [k for k in new_t if k in old_t and (
        new_t[k].get("target_end_year") != old_t[k].get("target_end_year")
        or new_t[k].get("value") != old_t[k].get("value"))]
    found_new = latest_found(new)
    pairs = [k for k in new_t if k in found_new]

    if new_targets or changed_targets or dropped_targets or pairs:
        L.append("## Target movements\n")
        if new_targets:
            L.append("**New targets**\n")
            for k in sorted(new_targets):
                t = new_t[k]
                L.append(f"- {k[0]} / {k[1]}: {t.get('value','')} by "
                         f"{t.get('target_end_year','') or '?'} "
                         f"(status: {t.get('target_status','') or 'n/a'})")
            L.append("")
        if changed_targets:
            L.append("**Changed targets**\n")
            for k in sorted(changed_targets):
                o, n = old_t[k], new_t[k]
                bits = []
                if o.get("target_end_year") != n.get("target_end_year"):
                    bits.append(f"end year {o.get('target_end_year','') or '?'} -> "
                                f"{n.get('target_end_year','') or '?'}")
                if o.get("value") != n.get("value"):
                    bits.append(f"value {o.get('value','') or '?'} -> "
                                f"{n.get('value','') or '?'}")
                L.append(f"- {k[0]} / {k[1]}: " + "; ".join(bits))
            L.append("")
        if dropped_targets:
            L.append("**Dropped targets** (verify achieved vs abandoned)\n")
            for k in sorted(dropped_targets):
                o = old_t[k]
                L.append(f"- {k[0]} / {k[1]}: was {o.get('value','')} by "
                         f"{o.get('target_end_year','') or '?'}")
            L.append("")
        if pairs:
            L += ["**Target vs latest actual**\n",
                  "| entity | indicator | target | actual | end year | status |",
                  "|---|---|---|---|---|---|"]
            for k in sorted(pairs):
                t, a = new_t[k], found_new[k]
                L.append(f"| {k[0]} | {k[1]} | {t.get('value','')} | "
                         f"{a.get('value','')} | {t.get('target_end_year','')} | "
                         f"{t.get('target_status','') or ''} |")
            L.append("")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd esg-longitudinal && python -m pytest tests/test_diff.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add esg-longitudinal/scripts/diff.py esg-longitudinal/tests/test_diff.py
git commit -m "Add target-movements section to diff.py"
```

---

### Task 3: Enabler taxonomy in the 10R reference

**Files:**
- Modify: `esg-longitudinal/circular-economy-10rs.json`
- Test: `esg-longitudinal/tests/test_reference_data.py`

**Interfaces:**
- Consumes: `snapshot.VALID_ENABLER` (cross-check that JSON ids match the validator).
- Produces: a top-level `enablers` array of 8 objects `{id, name, description, supports:[...]}`.

- [ ] **Step 1: Write the failing test**

Create `esg-longitudinal/tests/test_reference_data.py`:

```python
import json
import pathlib

from conftest import _load

snapshot = _load("snapshot")
ROOT = pathlib.Path(__file__).resolve().parents[1]

EXPECTED_ENABLERS = {"ecodesign", "rnd", "data_infrastructure", "training",
                     "partnerships", "reverse_logistics", "finance", "policy"}
VALID_R = {f"R{i}" for i in range(10)}


def test_enablers_present_and_well_formed():
    data = json.loads((ROOT / "circular-economy-10rs.json").read_text(encoding="utf-8"))
    enablers = {e["id"] for e in data["enablers"]}
    assert enablers == EXPECTED_ENABLERS
    for e in data["enablers"]:
        assert e["name"] and e["description"]
        assert e["supports"] and all(r in VALID_R for r in e["supports"])


def test_enabler_ids_match_snapshot_validator():
    data = json.loads((ROOT / "circular-economy-10rs.json").read_text(encoding="utf-8"))
    assert {e["id"] for e in data["enablers"]} == snapshot.VALID_ENABLER
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd esg-longitudinal && python -m pytest tests/test_reference_data.py -v`
Expected: FAIL — `KeyError: 'enablers'`.

- [ ] **Step 3: Add the enablers array**

In `esg-longitudinal/circular-economy-10rs.json`, add a top-level `"enablers"` key after the `"categories"` array (insert a comma after the closing `]` of `categories`):

```json
  "enablers": [
    {"id": "ecodesign", "name": "Design for circularity",
     "description": "Durability, modularity, recyclability, and disassembly designed into the product so downstream R-strategies become possible.",
     "supports": ["R2", "R3", "R4", "R5", "R8"]},
    {"id": "rnd", "name": "R&D and innovation",
     "description": "Research into circular materials, processes, and technologies (e.g. recycled feedstock, bio-based inputs).",
     "supports": ["R2", "R5", "R6", "R8", "R9"]},
    {"id": "data_infrastructure", "name": "Digital and data infrastructure",
     "description": "Digital product passports, material traceability, IoT tracking, and the data systems that make loops auditable.",
     "supports": ["R3", "R4", "R5", "R8"]},
    {"id": "training", "name": "Workforce skills and training",
     "description": "Building the skills and capability needed to repair, refurbish, and run circular operations.",
     "supports": ["R4", "R5", "R6"]},
    {"id": "partnerships", "name": "Partnerships and collaboration",
     "description": "Value-chain collaboration, take-back networks, industry consortia, and customer engagement.",
     "supports": ["R1", "R3", "R7", "R8"]},
    {"id": "reverse_logistics", "name": "Reverse logistics",
     "description": "Collection, take-back, and sorting infrastructure that returns used products and materials into loops.",
     "supports": ["R3", "R4", "R5", "R6", "R8"]},
    {"id": "finance", "name": "Circular finance and business models",
     "description": "Financing circular business models (product-as-a-service, leasing) and capital for circular production lines.",
     "supports": ["R1", "R2"]},
    {"id": "policy", "name": "Policy and standards engagement",
     "description": "Policy advocacy and regulatory alignment such as extended producer responsibility and right-to-repair.",
     "supports": ["R0", "R4", "R8"]}
  ]
```

- [ ] **Step 4: Run tests + JSON validity check**

Run: `cd esg-longitudinal && python -c "import json; json.load(open('circular-economy-10rs.json', encoding='utf-8')); print('json ok')" && python -m pytest tests/test_reference_data.py -v`
Expected: `json ok` then PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add esg-longitudinal/circular-economy-10rs.json esg-longitudinal/tests/test_reference_data.py
git commit -m "Add circular-economy enabler taxonomy"
```

---

### Task 4: R-strategy hints in indicators.yaml

**Files:**
- Modify: `esg-longitudinal/references/indicators.yaml`
- Test: `esg-longitudinal/tests/test_reference_data.py` (add one test)

**Interfaces:**
- Consumes: nothing (regex scan avoids a YAML dependency).
- Produces: an `r_hint` on each `circular:` indicator line.

- [ ] **Step 1: Add the failing test**

Append to `esg-longitudinal/tests/test_reference_data.py`:

```python
import re


def test_circular_indicators_have_valid_r_hint():
    text = (ROOT / "references" / "indicators.yaml").read_text(encoding="utf-8")
    circular = text.split("circular:", 1)[1].split("\nbiodiversity:", 1)[0]
    hints = re.findall(r"r_hint:\s*([R0-9|]+)", circular)
    assert len(hints) >= 8  # one per circular indicator
    for h in hints:
        for tok in h.split("|"):
            assert re.fullmatch(r"R[0-9]", tok), f"bad r_hint token {tok}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd esg-longitudinal && python -m pytest tests/test_reference_data.py::test_circular_indicators_have_valid_r_hint -v`
Expected: FAIL — `assert len(hints) >= 8` (0 found).

- [ ] **Step 3: Add r_hint to each circular indicator**

In `esg-longitudinal/references/indicators.yaml`, replace the `circular:` block with:

```yaml
circular:
  # r_hint = the R-strategy(ies) an indicator typically evidences (guidance for the
  # r_strategy column; pipe-separated, primary first). See circular-economy-10rs.json.
  - {indicator: circular_revenue_pct,            unit: "%",     r_hint: R1|R2}
  - {indicator: recycled_input_pct,              unit: "%",     r_hint: R8}
  - {indicator: waste_recycled_pct,              unit: "%",     r_hint: R8}
  - {indicator: waste_to_landfill_pct,           unit: "%",     r_hint: R8|R9}
  - {indicator: total_waste,                     unit: tonnes,  r_hint: R2}
  - {indicator: product_takeback_scope,          unit: text,    r_hint: R3|R6}
  - {indicator: closed_loop_target_year,         unit: year,    r_hint: R8}
  - {indicator: material_circularity_rate,       unit: "%",     r_hint: R2|R8}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd esg-longitudinal && python -m pytest tests/test_reference_data.py -v`
Expected: PASS (3 tests total in file).

- [ ] **Step 5: Commit**

```bash
git add esg-longitudinal/references/indicators.yaml esg-longitudinal/tests/test_reference_data.py
git commit -m "Add r_hint guidance to circular indicators"
```

---

### Task 5: Document the classification layer in SKILL.md

**Files:**
- Modify: `esg-longitudinal/SKILL.md`
- Test: `esg-longitudinal/tests/test_reference_data.py` (add one doc-presence test)

**Interfaces:**
- Consumes: nothing.
- Produces: updated schema block + three new sections + report template additions.

- [ ] **Step 1: Add the failing doc-presence test**

Append to `esg-longitudinal/tests/test_reference_data.py`:

```python
def test_skill_documents_new_columns():
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    for col in ["item_type", "r_strategy", "enabler_topic",
                "target_end_year", "target_has_kpi", "target_status"]:
        assert col in text, f"SKILL.md missing {col}"
    for section in ["Classification layer", "Target anatomy",
                    "Year-over-year target status"]:
        assert section in text, f"SKILL.md missing section: {section}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd esg-longitudinal && python -m pytest tests/test_reference_data.py::test_skill_documents_new_columns -v`
Expected: FAIL — SKILL.md missing `item_type`.

- [ ] **Step 3: Update the schema block**

In `esg-longitudinal/SKILL.md`, replace the fenced schema block under "The tidy schema" (currently the 13-column list) with:

```
entity, lei, domain, indicator, value, unit, period, status,
source, source_url, page, quote, retrieved_at,
item_type, r_strategy, enabler_topic, target_end_year, target_has_kpi, target_status
```

Immediately after the existing `status =` and `quote =` bullets, add:

```markdown
- `item_type` = `kpi` (a measured metric/indicator) | `target` (a forward-looking
  goal) | `qualitative` (a narrative commitment with no number). Auto-filled from
  `status` when blank (found→kpi, target→target).
- `r_strategy` = which of the 10 R-strategies (R0–R9) a circular commitment
  advances; pipe-separated with the primary first (e.g. `R2|R8`). See
  `circular-economy-10rs.json`. Blank for non-circular rows.
- `enabler_topic` = for commitments that *enable* circularity rather than being an
  R-strategy themselves (training, data infrastructure, R&D, …); one of the enabler
  ids in `circular-economy-10rs.json`. Independent of `r_strategy`.
- `target_end_year` / `target_has_kpi` = target completeness (see Target anatomy).
- `target_status` = year-over-year outcome (see Year-over-year target status).
```

- [ ] **Step 4: Add the three new sections**

In `esg-longitudinal/SKILL.md`, after the "Provenance and anti-hallucination" section, add:

```markdown
## Classification layer (circular economy)

Beyond the raw value, classify each circular row so the dataset is queryable and
comparable across companies:

- **R-strategy** (`r_strategy`): map the commitment to R0–R9 using
  `circular-economy-10rs.json`. Shorter loops (R0 Refuse … R2 Reduce) are more
  circular than long loops (R8 Recycle, R9 Recover). Use `references/indicators.yaml`
  `r_hint` as a starting point, but read the text — a "recycled content" pledge is
  R8, a "designed-out packaging" pledge is R0/R2. Pipe-separate when a commitment
  genuinely spans strategies (primary first).
- **Enablers** (`enabler_topic`): some commitments are not an R-strategy but make
  them possible — training, data/traceability infrastructure, R&D, partnerships,
  reverse logistics, finance, policy. Tag these with the matching enabler id. A row
  is usually an R-strategy item *or* an enabler item; occasionally both.

## Target anatomy

For `status=target` rows, record completeness so you can tell a hard commitment from
an aspiration:

- `target_end_year` — the deadline year (`2025`), or blank if none stated.
- `target_has_kpi` — `yes` if the target carries a quantified value/KPI ("25%
  circular revenue"), `no` if it is directional only ("become fully circular").
- Derived **completeness** (report only): `both` (kpi + year) → fully specified;
  `kpi_only` → no deadline; `year_only` → deadline but no metric; `none` → vague
  aspiration.

## Year-over-year target status

Once a target appears in more than one year, record what happened to it in
`target_status`:

- `on_track` — actuals moving toward the target, deadline unchanged.
- `achieved` — the target was met (an actual now meets/exceeds it).
- `delayed` — deadline pushed out.
- `changed` — target value or scope restated.
- `failed` — deadline passed without meeting the target.
- `dropped` — the target disappeared from disclosure.
- `too_early` — first year seen; not yet assessable.

Record this from what the report states. `scripts/diff.py` independently produces a
**Target movements** section (new / changed / dropped targets, plus a target-vs-actual
table) by comparing `status=target` rows across snapshots — use it to catch silent
changes the report does not admit, and reconcile against your `target_status`.
```

- [ ] **Step 5: Update the report template**

In the "Output: report structure" fenced block, add a target scorecard and a
movements line. Replace the template body with:

```markdown
# {Company} — {Domain} over time ({year range})

## Time series
| indicator | R | unit | {2015} | {2018} | {2020} | {2022} | target |
|---|---|---|---|---|---|---|---|
(one row per indicator; R = r_strategy; cells blank where not_found)

## Target scorecard
| indicator | target | end year | has KPI | completeness | status |
|---|---|---|---|---|---|
(one row per status=target; completeness derived from end year + has KPI)

## Enablers
(training / data infrastructure / R&D / … commitments, grouped by enabler_topic)

## What changed since last snapshot
(diff.py output — including the Target movements section — or
"first snapshot — baseline established")

## Notable trajectory
(2–4 sentences: direction of travel, gaps, restatements, target vs actual)

## Sources
(every report used, with URL and year)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd esg-longitudinal && python -m pytest tests/test_reference_data.py -v`
Expected: PASS (4 tests total in file).

- [ ] **Step 7: Commit**

```bash
git add esg-longitudinal/SKILL.md esg-longitudinal/tests/test_reference_data.py
git commit -m "Document classification layer in SKILL.md"
```

---

### Task 6: Migrate eval assertions to the 19-column schema

**Files:**
- Modify: `esg-longitudinal/evals/evals.json`
- Test: `esg-longitudinal/tests/test_reference_data.py` (add eval-migration test)

**Interfaces:**
- Consumes: nothing.
- Produces: updated eval assertions (no "13-column"), a 19-column header assertion, classification assertions, and a new eval-4.

- [ ] **Step 1: Add the failing test**

Append to `esg-longitudinal/tests/test_reference_data.py`:

```python
def test_evals_reference_19_column_schema():
    data = json.loads((ROOT / "evals" / "evals.json").read_text(encoding="utf-8"))
    blob = json.dumps(data)
    assert "13-column" not in blob and "13 column" not in blob
    # the full 19-column header appears verbatim in at least one assertion
    header = ("entity,lei,domain,indicator,value,unit,period,status,source,"
              "source_url,page,quote,retrieved_at,item_type,r_strategy,"
              "enabler_topic,target_end_year,target_has_kpi,target_status")
    assert header in blob
    # a classification assertion exists
    assert "r_strategy" in blob and "target_status" in blob
    ids = [e["id"] for e in data["evals"]]
    assert 4 in ids  # new longitudinal target-status eval
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd esg-longitudinal && python -m pytest tests/test_reference_data.py::test_evals_reference_19_column_schema -v`
Expected: FAIL — `assert "13-column" not in blob` (evals still say 13-column).

- [ ] **Step 3: Update eval 1, 2, 3 assertions**

In `esg-longitudinal/evals/evals.json`:

Eval 1 — replace the assertion:
`"The snapshot CSV header is exactly the 13-column tidy schema: entity,lei,domain,indicator,value,unit,period,status,source,source_url,page,quote,retrieved_at."`
with:
`"The snapshot CSV header is exactly the 19-column tidy schema: entity,lei,domain,indicator,value,unit,period,status,source,source_url,page,quote,retrieved_at,item_type,r_strategy,enabler_topic,target_end_year,target_has_kpi,target_status."`

Eval 1 — add two assertions to its `assertions` array:
`"status=target circular rows carry item_type=target and, where the report states a deadline, a four-digit target_end_year (e.g. circular_revenue_pct 25% with target_end_year=2025)."`
`"Circular found/target rows carry an r_strategy (R0-R9, e.g. recycled_input_pct=R8) or an enabler_topic (e.g. data_infrastructure), classifying the commitment."`

Eval 2 — replace the assertion:
`"A timestamped snapshot CSV is written under data/snapshots/ using the identical 13-column schema as eval 1 (no added, renamed, or dropped columns), proving schema reuse across a different company and domain."`
with:
`"A timestamped snapshot CSV is written under data/snapshots/ using the identical 19-column schema as eval 1 (no renamed or dropped columns), proving schema reuse across a different company and domain."`

Eval 3 — replace the assertion:
`"The new snapshot uses the identical 13-column schema and matching key fields (entity=Royal Philips, indicator names like circular_revenue_pct, period years) so the diff keys align with the baseline instead of showing spurious churn."`
with:
`"The new snapshot uses the 19-column schema and matching key fields (entity=Royal Philips, indicator names like circular_revenue_pct, period years); the diff keys (entity, indicator, period) align with the 13-column baseline via the shared columns instead of showing spurious churn."`

- [ ] **Step 4: Add eval 4**

In `esg-longitudinal/evals/evals.json`, append to the `evals` array:

```json
    {
      "id": 4,
      "name": "philips-target-status-2yr",
      "prompt": "Track Royal Philips' circular economy TARGETS specifically over the last two reporting years. For each target, tell me the R-strategy it maps to, whether it has a measurable KPI and a deadline year, and whether the prior year's target was achieved, delayed, changed, failed, or dropped.",
      "expected_output": "A snapshot with status=target rows for Philips circular targets across two years, each carrying r_strategy (e.g. R1|R2 for circular_revenue_pct), target_end_year, target_has_kpi, and a target_status classification (on_track|achieved|delayed|changed|failed|dropped|too_early). A report with a Target scorecard (completeness derived from end year + has KPI) and, where a prior snapshot exists, a diff.py Target movements section.",
      "files": [],
      "assertions": [
        "Snapshot status=target rows carry item_type=target, r_strategy (R0-R9), target_end_year (YYYY), and target_has_kpi (yes|no).",
        "At least one target row carries a target_status from the set on_track|achieved|delayed|changed|failed|dropped|too_early.",
        "The snapshot passes scripts/snapshot.py validation (exit code 0), i.e. all classification values are drawn from the documented enums.",
        "The report includes a Target scorecard table with a derived completeness (both|kpi_only|year_only|none) per target.",
        "When two years of targets are present, the report characterizes each prior target as achieved, delayed, changed, failed, or dropped rather than only listing current targets."
      ]
    }
```

(Add a comma after eval 3's closing `}` so the array stays valid JSON.)

- [ ] **Step 5: Run test + JSON validity**

Run: `cd esg-longitudinal && python -c "import json; json.load(open('evals/evals.json', encoding='utf-8')); print('json ok')" && python -m pytest tests/test_reference_data.py -v`
Expected: `json ok` then PASS.

- [ ] **Step 6: Commit**

```bash
git add esg-longitudinal/evals/evals.json esg-longitudinal/tests/test_reference_data.py
git commit -m "Migrate evals to 19-column schema + add target-status eval"
```

---

### Task 7: Full regression + branch verification

**Files:** none (verification only).

- [ ] **Step 1: Run the whole test suite**

Run: `cd esg-longitudinal && python -m pytest tests/ -v`
Expected: PASS — all tests across the three files (≈19 tests).

- [ ] **Step 2: End-to-end smoke test of snapshot + diff with real classification data**

Run:
```bash
cd esg-longitudinal
python - <<'PY'
import json, subprocess, sys, os
os.makedirs("data/snapshots", exist_ok=True)
rows = [
 {"entity":"Royal Philips","domain":"circular","indicator":"circular_revenue_pct","value":"25","unit":"%","period":"2022","status":"target","source":"AR2022","source_url":"http://x","page":"p41","quote":"target 25% by 2025","item_type":"target","r_strategy":"R1|R2","target_end_year":"2025","target_has_kpi":"yes","target_status":"on_track"},
 {"entity":"Royal Philips","domain":"circular","indicator":"circular_revenue_pct","value":"18","unit":"%","period":"2022","status":"found","source":"AR2022","source_url":"http://x","page":"p41","quote":"18% circular revenues","r_strategy":"R1|R2"},
]
json.dump(rows, open("data/_rows.json","w"))
print(subprocess.run([sys.executable,"scripts/snapshot.py","--rows","data/_rows.json","--run-date","2026-07-02"],capture_output=True,text=True).stdout)
PY
```
Expected: `wrote 2 rows -> data/snapshots/2026-07-02.csv`, exit 0 (proves classification rows pass validation end-to-end).

- [ ] **Step 3: Clean up smoke-test artifacts**

Run: `cd esg-longitudinal && rm -f data/_rows.json data/snapshots/2026-07-02.csv`
(Leave `data/` empty; snapshots are user-run outputs, not committed.)

- [ ] **Step 4: Final commit if anything changed**

```bash
git status
# if the smoke test left nothing, no commit needed; otherwise:
# git add -A && git commit -m "..."
```

---

## Self-Review

**Spec coverage** (checked against `2026-07-02-esg-circular-classification-design.md`):
- 6 new columns → Task 1 ✓
- Enabler taxonomy (8) → Task 3 ✓
- item_type / target completeness → Task 1 (columns) + Task 5 (derived completeness doc) ✓
- Year-over-year target_status + diff Target movements → Task 1 (column) + Task 2 (diff) + Task 5 (doc) ✓
- r_hint in indicators.yaml → Task 4 ✓
- SKILL.md schema + sections + report template → Task 5 ✓
- Evals 13→19 migration + eval-4 → Task 6 ✓
- Backward compatibility (old rows pass, KEY unchanged) → Task 1 test `test_backward_compatible_13col_row_passes`, Task 2 preserves KEY ✓

**Type consistency:** `COLS`, `validate`, `_autofill_item_type`, `VALID_ENABLER` (Task 1) reused verbatim in Tasks 2–3 tests. `target_rows`/`latest_found`/`TKEY` defined once in Task 2. Enabler id set identical in JSON (Task 3), validator (Task 1), and tests. Header string identical in plan constraints, Task 1 test, Task 5 doc, Task 6 eval + test.

**Placeholder scan:** No TBD/TODO; every code step shows full code; every run step shows expected output. Clean.
