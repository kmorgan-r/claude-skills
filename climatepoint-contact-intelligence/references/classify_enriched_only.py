#!/usr/bin/env python3
"""
Classify only rows that have enrichment (Title or Summary) AND empty Persona.

Skips un-enriched rows so they don't get a meaningless "Low-fit / Other"
stamp from a rubric that ran without data.

Usage:
    python classify_enriched_only.py \
        --input  v13.csv \
        --output v13_classified.csv
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import os
import sys
import tempfile
from typing import Dict


def _atomic_write_csv(path, fieldnames, rows, extrasaction="ignore"):
    """Write CSV atomically: stream to a temp file in the same dir, then
    os.replace() onto `path`. A crash mid-write truncates the temp (not
    `path`), so the prior checkpoint survives and --resume sees a complete
    CSV instead of a partial write."""
    out_dir = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".csv", dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as out:
            w = csv.DictWriter(out, fieldnames=fieldnames, extrasaction=extrasaction)
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


HERE = os.path.dirname(os.path.abspath(__file__))
CLASSIFIER_PATH = os.path.join(HERE, "climatepoint_classifier.py")

spec = importlib.util.spec_from_file_location("cpc", CLASSIFIER_PATH)
cpc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpc)


def has_enrichment(row: Dict[str, str]) -> bool:
    title = (row.get("Title") or "").strip()
    summary = (row.get("Summary") or "").strip()
    if title and title.lower() not in ("unknown", "not found", "generic contact"):
        return True
    if summary and len(summary) >= 20:
        return True
    return False


def classify_one(row: Dict[str, str]) -> None:
    title = (row.get("Title") or "").strip()
    headline = (row.get("Headline") or "").strip()
    summary = (row.get("Summary") or "").strip()
    company = (row.get("Company") or "").strip()
    domain = (row.get("Domain") or "").strip()
    if not domain:
        domain = cpc.extract_domain(row.get("Email", ""))

    persona = cpc.rule_based_persona(title, headline, summary, domain)
    if not persona:
        persona = "Unknown"

    seniority = cpc.detect_seniority(title)
    score = cpc.score_lead(persona, seniority, title, company, summary, domain)
    need = cpc.classify_need(persona, title, company, summary)
    opportunity = cpc.map_opportunity(persona, need)
    angle = cpc.build_outreach_angle(persona, company, need, ollama=None, title=title, summary=summary)
    next_action = cpc.determine_next_action(score, persona)

    if not (row.get("Persona") or "").strip():
        row["Persona"] = persona
    if not (row.get("Lead Score (1-10)") or "").strip():
        row["Lead Score (1-10)"] = str(score)
    if not (row.get("Need State") or "").strip():
        row["Need State"] = need
    if not (row.get("Opportunity Type") or "").strip():
        row["Opportunity Type"] = opportunity
    if not (row.get("Outreach Angle") or "").strip():
        row["Outreach Angle"] = angle
    if not (row.get("Next Action") or "").strip():
        row["Next Action"] = next_action
    if not (row.get("Seniority") or "").strip():
        row["Seniority"] = seniority


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    with open(args.input, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    classified = 0
    skipped_no_data = 0
    skipped_already = 0
    for row in rows:
        if (row.get("Persona") or "").strip():
            skipped_already += 1
            continue
        if not has_enrichment(row):
            skipped_no_data += 1
            continue
        classify_one(row)
        classified += 1

    _atomic_write_csv(args.output, fieldnames, rows)

    print(f"classified: {classified}")
    print(f"skipped (already classified): {skipped_already}")
    print(f"skipped (no enrichment): {skipped_no_data}")
    print(f"output: {args.output}")


if __name__ == "__main__":
    main()
