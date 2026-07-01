#!/usr/bin/env python3
"""
Merge multiple part CSVs from parallel tavily_enrich runs.

Each part has the full row set but only its assigned slice was enriched.
For each row, take the version with the most enrichment (longest
Title+Summary), preferring later parts on tie.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
from typing import Dict, List


def _csv_safe(value):
    """Neutralize CSV formula injection (OWASP). Enriched fields (Company,
    Title, Summary, Headline, search snippets) come from live web-search
    results — untrusted text. A leading = + - @ turns a cell into a live
    Excel formula/DDE payload when the output is opened in Excel (SKILL.md
    ships utf-8-sig for exactly that). Prefix such cells with a single quote
    so Excel treats them as literal text. None preserved (csv writes "")."""
    if value is None:
        return value
    s = str(value)
    if s[:1] in ("=", "+", "-", "@"):
        return "'" + s
    return s


def _atomic_write_csv(path, fieldnames, rows, extrasaction="ignore"):
    """Write CSV atomically: stream to a temp file in the same dir, then
    os.replace() onto `path`. A crash mid-write truncates the temp (not
    `path`), so the prior checkpoint survives and --resume sees a complete
    CSV instead of a partial write. Cell values are sanitized against CSV
    formula injection before writing."""
    out_dir = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".csv", dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as out:
            w = csv.DictWriter(out, fieldnames=fieldnames, extrasaction=extrasaction)
            w.writeheader()
            w.writerows([{k: _csv_safe(v) for k, v in row.items()} for row in rows])
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


ENRICH_FIELDS = ("Title", "LinkedIn", "Company", "Summary", "Headline")


def enrichment_score(row: Dict[str, str]) -> int:
    return sum(len((row.get(k) or "").strip()) for k in ENRICH_FIELDS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="baseline CSV (v13b)")
    ap.add_argument("--parts", nargs="+", required=True, help="part CSVs to merge")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    with open(args.base, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        base_rows = list(reader)

    n = len(base_rows)
    print(f"base rows: {n}")

    merged = [dict(r) for r in base_rows]

    for path in args.parts:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            part = list(csv.DictReader(f))
        if len(part) != n:
            print(f"WARNING: {path} has {len(part)} rows, expected {n}")
        improved = 0
        for i, prow in enumerate(part):
            if i >= n:
                break
            if enrichment_score(prow) > enrichment_score(merged[i]):
                merged[i] = prow
                improved += 1
        print(f"{path}: improved {improved} rows")

    _atomic_write_csv(args.output, fieldnames, merged)

    enriched_total = sum(1 for r in merged if (r.get("Title") or "").strip() or (r.get("Summary") or "").strip())
    print(f"output: {args.output}")
    print(f"rows with Title or Summary: {enriched_total}")


if __name__ == "__main__":
    main()
