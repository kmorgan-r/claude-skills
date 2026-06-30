#!/usr/bin/env python3
"""
Serper.dev backend for contact enrichment. Reuses all parsing logic
from tavily_enrich.py by monkey-patching the search function.

Serper response (organic[].title/link/snippet) is mapped to Tavily-shape
(results[].title/url/content) so find_linkedin_url, first_non_linkedin_summary,
extract_title_from_snippet etc. work unchanged.

Usage:
    python serper_enrich.py \
        --input  v14b.csv \
        --output v15_part1.csv \
        --serper-key 9e8c... \
        --start-row 0 --limit 100
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import os
import sys
import time
from typing import Any, Dict, List

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("te", os.path.join(HERE, "tavily_enrich.py"))
te = importlib.util.module_from_spec(spec)
spec.loader.exec_module(te)

SERPER_URL = "https://google.serper.dev/search"


def serper_search(api_key: str, query: str, max_results: int = 5, timeout: int = 20) -> List[Dict[str, Any]]:
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    payload = {"q": query, "num": max_results}
    try:
        r = requests.post(SERPER_URL, headers=headers, json=payload, timeout=timeout)
        r.raise_for_status()
        data = r.json() or {}
    except Exception as e:
        print(f"  [serper error] {e}", file=sys.stderr)
        return []
    out: List[Dict[str, Any]] = []
    for item in data.get("organic", []) or []:
        out.append({
            "title": item.get("title", "") or "",
            "url": item.get("link", "") or "",
            "content": item.get("snippet", "") or "",
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--serper-key", default=os.getenv("SERPER_API_KEY"))
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--save-every", type=int, default=10)
    ap.add_argument("--start-row", type=int, default=0)
    ap.add_argument("--end-row", type=int, default=10**9)
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    if not args.serper_key:
        sys.exit("Missing --serper-key or SERPER_API_KEY")

    # Patch tavily_search so enrich_row routes through Serper.
    api_key = args.serper_key
    te.tavily_search = lambda _k, q, max_results=5, timeout=20: serper_search(api_key, q, max_results, timeout)

    with open(args.input, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    print(f"loaded {len(rows)} rows")

    targets: List[int] = []
    for i, row in enumerate(rows):
        if i < args.start_row or i > args.end_row:
            continue
        if (row.get("Persona") or "").strip():
            continue
        email = (row.get("Email") or "").strip()
        domain = (row.get("Domain") or "").strip()
        title = (row.get("Title") or "").strip()
        if not email and not domain:
            continue
        if title and title.lower() not in ("unknown", "not found", "generic contact"):
            continue
        targets.append(i)
        if len(targets) >= args.limit:
            break

    print(f"enriching {len(targets)} rows [{args.start_row}..{args.end_row}] limit={args.limit}")

    def flush():
        with open(args.output, "w", encoding="utf-8-sig", newline="") as out:
            w = csv.DictWriter(out, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"  [saved] {args.output}", flush=True)

    stats = {"ok": 0, "no_results": 0, "skip": 0, "changed_fields": 0}
    t0 = time.time()
    for n, idx in enumerate(targets, start=1):
        row = rows[idx]
        first = row.get("First Name", "")
        last = row.get("Last Name", "")
        domain = row.get("Domain", "")
        print(f"[{n}/{len(targets)}] row {idx}: {first} {last} ({domain})", flush=True)
        try:
            res = te.enrich_row(row, api_key)
        except Exception as e:
            print(f"  [error] {e}", file=sys.stderr)
            res = {"status": "skip", "reason": str(e)}
        status = res.get("status", "skip")
        stats[status] = stats.get(status, 0) + 1
        changed = res.get("changed", [])
        if changed:
            stats["changed_fields"] += len(changed)
            print(f"  -> {', '.join(changed)}", flush=True)
        else:
            print(f"  -> no changes ({status})", flush=True)
        if n % args.save_every == 0:
            flush()
        time.sleep(args.sleep)

    flush()
    print(f"\nDone in {time.time()-t0:.1f}s. stats={stats}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
