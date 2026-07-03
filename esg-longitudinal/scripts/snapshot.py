#!/usr/bin/env python3
"""Append normalized indicator rows to a timestamped snapshot CSV.

The snapshot is the durable baseline that makes longitudinal diffing possible, so
run this on EVERY run (including the first) — a future run needs something to diff
against.

Schema (one row per company-indicator-period):
    entity, lei, domain, indicator, value, unit, period, status,
    source, source_url, page, quote, retrieved_at,
    item_type, r_strategy, enabler_topic, target_end_year, target_has_kpi, target_status,
    smart_specific, smart_achievable, smart_relevant, substance,
    planetary_alignment, impact_scope, priority_internal, importance_external,
    linked_targets, assessment_notes

Required on every row: entity, domain, indicator, period, status, retrieved_at.
If status == "found" or "target", then value + source_url + quote are also required
(anti-hallucination: a value with no source and no quote is not a value).
status is one of: found | not_found | target

Usage:
    python snapshot.py --rows rows.json --run-date 2026-06-29
    # -> data/snapshots/2026-06-29.csv  (appends if it already exists)

rows.json is a list of row dicts, or {"rows": [...]}.
"""
import argparse
import csv
import json
import os
import re
import sys

COLS = ["entity", "lei", "domain", "indicator", "value", "unit", "period", "status",
        "source", "source_url", "page", "quote", "retrieved_at",
        "item_type", "r_strategy", "enabler_topic",
        "target_end_year", "target_has_kpi", "target_status",
        "smart_specific", "smart_achievable", "smart_relevant", "substance",
        "planetary_alignment", "impact_scope", "priority_internal", "importance_external",
        "linked_targets", "assessment_notes"]
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


def validate(row):
    errs = []
    for k in REQUIRED:
        if not str(row.get(k, "")).strip():
            errs.append(f"missing {k}")
    status = str(row.get("status", "")).strip()
    if status and status not in VALID_STATUS:
        errs.append(f"invalid status '{status}' (use found|not_found|target)")
    if status in SOURCED_STATUSES:
        for k in ("value", "source_url", "quote"):
            if not str(row.get(k, "")).strip():
                errs.append(f"status={status} requires {k}")

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

    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True, help="JSON file: list of row dicts")
    ap.add_argument("--run-date", required=True, help="YYYY-MM-DD (retrieved_at / snapshot date)")
    ap.add_argument("--out-dir", default="data/snapshots")
    args = ap.parse_args()

    with open(args.rows, encoding="utf-8") as f:
        rows = json.load(f)
    if isinstance(rows, dict):
        rows = rows.get("rows", [])

    bad = []
    for idx, r in enumerate(rows):
        if not str(r.get("retrieved_at", "")).strip():
            r["retrieved_at"] = args.run_date
        _autofill_item_type(r)
        errs = validate(r)
        if errs:
            bad.append((idx, errs))

    if bad:
        for idx, errs in bad:
            print(f"row {idx}: {'; '.join(errs)}", file=sys.stderr)
        print(f"\n{len(bad)} invalid row(s). Fix the data before snapshotting - "
              "never invent a value or quote just to satisfy the schema.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.out_dir, exist_ok=True)
    out = os.path.join(args.out_dir, f"{args.run_date}.csv")
    is_new = not os.path.exists(out)
    with open(out, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        if is_new:
            w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLS})

    print(f"wrote {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
