#!/usr/bin/env python3
"""Append normalized indicator rows to a timestamped snapshot CSV.

The snapshot is the durable baseline that makes longitudinal diffing possible, so
run this on EVERY run (including the first) — a future run needs something to diff
against.

Schema (one row per company-indicator-period):
    entity, lei, domain, indicator, value, unit, period, status,
    source, source_url, page, quote, retrieved_at

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
import sys

COLS = ["entity", "lei", "domain", "indicator", "value", "unit", "period", "status",
        "source", "source_url", "page", "quote", "retrieved_at"]
REQUIRED = ["entity", "domain", "indicator", "period", "status", "retrieved_at"]
SOURCED_STATUSES = {"found", "target"}
VALID_STATUS = {"found", "not_found", "target"}


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
