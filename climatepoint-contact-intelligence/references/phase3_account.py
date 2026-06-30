#!/usr/bin/env python3
"""
Phase 3: account research for rows with Lead Score >= --min-score.

Pure text heuristic on Company + Summary (no API). Calls infer_account_fields()
from climatepoint_classifier.py, preserves existing non-empty values.

Usage:
    python phase3_account.py \
        --input  v20.csv \
        --output v21.csv \
        --min-score 7
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "cpc", os.path.join(HERE, "climatepoint_classifier.py")
)
cpc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpc)


P3_FIELDS = [
    "Company Name", "Website", "Industry", "Company Size",
    "Revenue / Funding Stage", "Country / HQ", "Product Type",
    "Sustainability Claims", "Regulatory Exposure", "Has Physical Product",
    "Has Manufacturing / Supply Chain", "Has Investors / Portfolio",
    "Existing ESG Content", "Likely LCA Need", "Estimated Urgency",
    "Recommended Offer",
]


def parse_score(row):
    try:
        return int(row.get("Lead Score (1-10)", "0") or 0)
    except (ValueError, TypeError):
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--min-score", type=int, default=7)
    ap.add_argument("--save-every", type=int, default=500)
    args = ap.parse_args()

    with open(args.input, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    # Ensure Phase 3 columns exist
    for c in P3_FIELDS:
        if c not in fieldnames:
            fieldnames.append(c)

    def save():
        with open(args.output, "w", encoding="utf-8-sig", newline="") as out:
            w = csv.DictWriter(out, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"  [saved] {args.output}", flush=True)

    targets = [i for i, r in enumerate(rows) if parse_score(r) >= args.min_score]
    print(f"total={len(rows)} score>={args.min_score}: {len(targets)}", flush=True)

    field_changes = Counter()
    processed = 0
    fully_new = 0

    for n, idx in enumerate(targets, start=1):
        row = rows[idx]
        persona = (row.get("Persona") or "").strip()
        company = (row.get("Company") or "").strip()
        email = (row.get("Email") or "").strip()
        domain = (row.get("Domain") or cpc.extract_domain(email) or "").strip()
        summary = (row.get("Summary") or "").strip()

        # Skip rows with no usable context
        if not (company or summary or domain):
            continue

        before_blank = sum(1 for f in P3_FIELDS if not (row.get(f) or "").strip())

        account = cpc.infer_account_fields(row, persona, company, domain, summary)

        # set_if_empty pattern: preserve non-empty/meaningful existing
        STALE = {"unknown", "none found", "minimal", "", "not enough information"}
        for k, v in account.items():
            if k not in fieldnames:
                continue
            existing = str(row.get(k, "") or "").strip()
            if existing and existing.lower() not in STALE:
                continue
            if str(v).strip() and row.get(k, "") != v:
                row[k] = v
                field_changes[k] += 1

        after_blank = sum(1 for f in P3_FIELDS if not (row.get(f) or "").strip())
        if before_blank == len(P3_FIELDS) and after_blank < len(P3_FIELDS):
            fully_new += 1
        processed += 1

        if processed % args.save_every == 0:
            print(f"[{n}/{len(targets)}] processed={processed} fully_new={fully_new}", flush=True)
            save()

    save()
    print(f"\nDone. processed={processed} fully_new={fully_new}")
    print("Field changes:")
    for k, v in field_changes.most_common():
        print(f"  {k}: {v}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
