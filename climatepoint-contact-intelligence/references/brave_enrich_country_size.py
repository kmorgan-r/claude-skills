#!/usr/bin/env python3
"""
Brave Search pass to fill Country / HQ + Company Size for score>=7 rows.

Strategy:
  1. Filter hot rows (score>=7) where Country OR Size is unknown/stale.
  2. Dedup by Company — one query per unique company.
  3. Brave query: '"{Company}" headquarters employees', anchored with the
     row's Domain when present to disambiguate generic same-name companies.
  4. Parse country (suffix/city/word/TLD) + size (employee count regex) from
     title+description of top results.
  5. Confidence gate: if the row's domain TLD maps to a country that
     conflicts with the web-search-detected country, the query likely hit a
     same-named different company — skip writing Country and leave the
     company retryable (not locked in --attempted-log). Size is still applied.
  6. Broadcast result back to every row sharing that company.

Usage:
    python brave_enrich_country_size.py \
        --input  v22.csv \
        --output v23.csv \
        --brave-key BSA... \
        --sleep 1.1
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

import requests


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


def _extract_domain(email: str) -> str:
    """Lowercased domain from an email, or '' if none. Mirror of
    cpc.extract_domain — kept local so this script stays standalone (no
    classifier import). Defensive: in normal pipeline order Domain is
    already populated by Phase 2 before this Phase-3 script runs, but the
    fallback keeps the disambiguation anchor working on raw/out-of-order
    inputs that have no Domain column."""
    if not email or "@" not in (email or ""):
        return ""
    return (email or "").split("@")[-1].strip().lower()


BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

STALE = {"unknown", "none found", "minimal", "", "not enough information", "n/a"}

# ---------- Country detection ----------
COUNTRY_SUFFIXES: List[Tuple[str, str]] = [
    (r"\bASA\b", "Norway"), (r"\bAS\b", "Norway"),
    (r"\bAB\b", "Sweden"),
    (r"\bGmbH\b", "Germany"), (r"\bAG\b", "Germany"), (r"\bKG\b", "Germany"),
    (r"\bPLC\b", "United Kingdom"), (r"\bLLP\b", "United Kingdom"),
    (r"\bLimited\b", "United Kingdom"), (r"\bLtd\.?\b", "United Kingdom"),
    (r"\bInc\.?\b", "United States"), (r"\bLLC\b", "United States"),
    (r"\bCorp\.?\b", "United States"),
    (r"\bB\.?V\.?\b", "Netherlands"), (r"\bN\.?V\.?\b", "Netherlands"),
    (r"\bSARL\b", "France"), (r"\bSAS\b", "France"),
    (r"\bOyj\b", "Finland"), (r"\bOy\b", "Finland"),
    (r"\bA/S\b", "Denmark"), (r"\bApS\b", "Denmark"),
    (r"\bS\.?p\.?A\.?\b", "Italy"), (r"\bS\.?r\.?l\.?\b", "Italy"),
]

COUNTRY_WORDS = {
    "Norway": "Norway", "Sweden": "Sweden", "Denmark": "Denmark",
    "Finland": "Finland", "Iceland": "Iceland", "Germany": "Germany",
    "Austria": "Austria", "Switzerland": "Switzerland", "Netherlands": "Netherlands",
    "Belgium": "Belgium", "France": "France", "Spain": "Spain", "Italy": "Italy",
    "Poland": "Poland", "Ireland": "Ireland", "Portugal": "Portugal",
    "United Kingdom": "United Kingdom", "England": "United Kingdom",
    "Scotland": "United Kingdom", "Wales": "United Kingdom",
    "United States": "United States", "USA": "United States",
    "Canada": "Canada", "Australia": "Australia", "India": "India",
    "Singapore": "Singapore", "Japan": "Japan", "China": "China",
    "Brazil": "Brazil", "Mexico": "Mexico", "Israel": "Israel",
    "South Africa": "South Africa", "New Zealand": "New Zealand",
}

CITY_TO_COUNTRY = {
    "Oslo": "Norway", "Bergen": "Norway", "Trondheim": "Norway",
    "Stavanger": "Norway", "Drammen": "Norway", "Kristiansand": "Norway",
    "Stockholm": "Sweden", "Gothenburg": "Sweden", "Göteborg": "Sweden",
    "Malmö": "Sweden", "Malmo": "Sweden", "Uppsala": "Sweden",
    "Copenhagen": "Denmark", "København": "Denmark", "Aarhus": "Denmark",
    "Helsinki": "Finland", "Espoo": "Finland", "Tampere": "Finland",
    "Reykjavik": "Iceland", "Reykjavík": "Iceland",
    "Berlin": "Germany", "Munich": "Germany", "München": "Germany",
    "Hamburg": "Germany", "Frankfurt": "Germany", "Cologne": "Germany",
    "Köln": "Germany", "Stuttgart": "Germany", "Düsseldorf": "Germany",
    "London": "United Kingdom", "Manchester": "United Kingdom",
    "Edinburgh": "United Kingdom", "Glasgow": "United Kingdom",
    "Bristol": "United Kingdom", "Cambridge": "United Kingdom",
    "Oxford": "United Kingdom",
    "New York": "United States", "San Francisco": "United States",
    "Boston": "United States", "Chicago": "United States",
    "Los Angeles": "United States", "Seattle": "United States",
    "Austin": "United States", "Denver": "United States",
    "Atlanta": "United States", "Houston": "United States",
    "Palo Alto": "United States", "Menlo Park": "United States",
    "Cambridge, MA": "United States", "Brooklyn": "United States",
    "Amsterdam": "Netherlands", "Rotterdam": "Netherlands",
    "Utrecht": "Netherlands", "The Hague": "Netherlands",
    "Paris": "France", "Lyon": "France", "Marseille": "France",
    "Brussels": "Belgium", "Antwerp": "Belgium",
    "Madrid": "Spain", "Barcelona": "Spain", "Valencia": "Spain",
    "Milan": "Italy", "Rome": "Italy", "Turin": "Italy",
    "Zurich": "Switzerland", "Geneva": "Switzerland", "Basel": "Switzerland",
    "Vienna": "Austria", "Warsaw": "Poland", "Dublin": "Ireland",
    "Lisbon": "Portugal", "Porto": "Portugal",
    "Toronto": "Canada", "Vancouver": "Canada", "Montreal": "Canada",
    "Sydney": "Australia", "Melbourne": "Australia",
    "Mumbai": "India", "Bangalore": "India", "Bengaluru": "India",
    "Delhi": "India", "Hyderabad": "India",
    "Singapore": "Singapore", "Tokyo": "Japan", "Tel Aviv": "Israel",
}

TLD_TO_COUNTRY = {
    "no": "Norway", "se": "Sweden", "dk": "Denmark", "fi": "Finland",
    "is": "Iceland", "de": "Germany", "at": "Austria", "ch": "Switzerland",
    "uk": "United Kingdom", "fr": "France", "es": "Spain", "it": "Italy",
    "nl": "Netherlands", "be": "Belgium", "ie": "Ireland", "pl": "Poland",
    "jp": "Japan", "cn": "China", "in": "India", "sg": "Singapore",
    "au": "Australia", "ca": "Canada", "br": "Brazil", "mx": "Mexico",
    "za": "South Africa", "il": "Israel", "pt": "Portugal", "nz": "New Zealand",
}


def detect_country(text: str, domain: str = "") -> Optional[str]:
    """Apply suffix → city → country word → TLD priority."""
    for pat, country in COUNTRY_SUFFIXES:
        if re.search(pat, text):
            return country
    for city in sorted(CITY_TO_COUNTRY, key=len, reverse=True):
        if re.search(rf"\b{re.escape(city)}\b", text):
            return CITY_TO_COUNTRY[city]
    for word, country in COUNTRY_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", text):
            return country
    if domain and "." in domain:
        tld = domain.rsplit(".", 1)[-1].lower()
        if tld in TLD_TO_COUNTRY:
            return TLD_TO_COUNTRY[tld]
    return None


# ---------- Size detection ----------
# LinkedIn-style ranges first (most reliable), then plain N employees.
SIZE_RANGE_PATS = [
    (re.compile(r"\b1[\-–]10\s+employees\b", re.I), "Micro (<10)"),
    (re.compile(r"\b2[\-–]10\s+employees\b", re.I), "Micro (<10)"),
    (re.compile(r"\b11[\-–]50\s+employees\b", re.I), "SME (10-200)"),
    (re.compile(r"\b51[\-–]200\s+employees\b", re.I), "SME (10-200)"),
    (re.compile(r"\b201[\-–]500\s+employees\b", re.I), "Mid-market (200-1000)"),
    (re.compile(r"\b501[\-–]1,?000\s+employees\b", re.I), "Mid-market (200-1000)"),
    (re.compile(r"\b1,?001[\-–]5,?000\s+employees\b", re.I), "Large enterprise (1000+)"),
    (re.compile(r"\b5,?001[\-–]10,?000\s+employees\b", re.I), "Large enterprise (1000+)"),
    (re.compile(r"\b10,?001\+?\s+employees\b", re.I), "Large enterprise (1000+)"),
]

# Fallback: "N employees" / "team of N" / "N+ people"
SIZE_COUNT_RE = re.compile(
    r"\b(\d{1,3}(?:,\d{3})*|\d{1,6})\s*\+?\s*"
    r"(?:employees|fte|people|staff|team members|professionals|"
    r"strong team|workforce)\b",
    re.I,
)


def bucket_count(n: int) -> str:
    if n < 10:
        return "Micro (<10)"
    if n < 200:
        return "SME (10-200)"
    if n < 1000:
        return "Mid-market (200-1000)"
    return "Large enterprise (1000+)"


def detect_size(text: str) -> Optional[str]:
    for pat, bucket in SIZE_RANGE_PATS:
        if pat.search(text):
            return bucket
    m = SIZE_COUNT_RE.search(text)
    if m:
        try:
            n = int(m.group(1).replace(",", ""))
            if 1 <= n <= 1_000_000:
                return bucket_count(n)
        except ValueError:
            pass
    return None


# ---------- Brave ----------
def brave_search(api_key: str, query: str, max_results: int = 5,
                 timeout: int = 20) -> List[Dict[str, Any]]:
    headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}
    params = {"q": query, "count": max_results}
    try:
        r = requests.get(BRAVE_URL, headers=headers, params=params, timeout=timeout)
        if r.status_code == 429:
            time.sleep(2.0)
            r = requests.get(BRAVE_URL, headers=headers, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json() or {}
    except Exception as e:
        print(f"  [brave error] {e}", file=sys.stderr)
        return []
    out = []
    for item in (data.get("web", {}) or {}).get("results", []) or []:
        out.append({
            "title": item.get("title", "") or "",
            "url": item.get("url", "") or "",
            "content": item.get("description", "") or "",
        })
    return out


def parse_score(row):
    try:
        return int(row.get("Lead Score (1-10)", "0") or 0)
    except (ValueError, TypeError):
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--brave-key", default=os.getenv("BRAVE_API_KEY"))
    ap.add_argument("--min-score", type=int, default=7)
    ap.add_argument("--sleep", type=float, default=1.1)
    ap.add_argument("--save-every", type=int, default=25)
    ap.add_argument("--max-queries", type=int, default=1800,
                    help="Cap to stay under Brave free 2000/mo quota")
    ap.add_argument("--start", type=int, default=0,
                    help="Start at this index in the sorted-unique companies list")
    ap.add_argument("--count", type=int, default=10**9,
                    help="Process at most this many companies this run")
    ap.add_argument("--attempted-log", default="",
                    help="JSON file of already-attempted-no-result company names; skip these and append new failures")
    args = ap.parse_args()

    import json
    attempted: set = set()
    if args.attempted_log and os.path.exists(args.attempted_log):
        try:
            with open(args.attempted_log, "r", encoding="utf-8") as f:
                attempted = set(json.load(f))
            print(f"  loaded {len(attempted)} attempted-no-result companies",
                  flush=True)
        except Exception as e:
            print(f"  [attempted-log read error] {e}", file=sys.stderr)

    if not args.brave_key:
        sys.exit("Missing --brave-key or BRAVE_API_KEY")

    with open(args.input, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    def save():
        _atomic_write_csv(args.output, fieldnames, rows)
        print(f"  [saved] {args.output}", flush=True)

    # Index hot rows by company
    company_rows: Dict[str, List[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        if parse_score(r) < args.min_score:
            continue
        company = (r.get("Company") or "").strip()
        if not company:
            continue
        country = (r.get("Country / HQ") or "").strip().lower()
        size = (r.get("Company Size") or "").strip().lower()
        if country in STALE or size in STALE:
            company_rows[company].append(i)

    companies = sorted(c for c in company_rows.keys() if c not in attempted)
    print(f"unique companies needing enrichment: {len(companies)} "
          f"(skipping {len(attempted)} prior-attempted)", flush=True)
    print(f"total rows touched (broadcast): "
          f"{sum(len(v) for v in company_rows.values())}", flush=True)

    if len(companies) > args.max_queries:
        print(f"  capping at {args.max_queries} (quota guard)", flush=True)
        companies = companies[: args.max_queries]

    end = min(len(companies), args.start + args.count)
    chunk = companies[args.start:end]
    print(f"this run: indices {args.start}..{end} ({len(chunk)} companies)",
          flush=True)
    companies = chunk

    stats = Counter()
    queries_done = 0

    for n, company in enumerate(companies, start=1):
        sample_idx = company_rows[company][0]
        domain = (rows[sample_idx].get("Domain")
                  or _extract_domain(rows[sample_idx].get("Email", "")) or "").strip()
        if domain:
            query = f'"{company}" {domain} headquarters employees'
        else:
            query = f'"{company}" headquarters employees'
        results = brave_search(args.brave_key, query)
        queries_done += 1
        stats["queries"] += 1

        if not results:
            stats["no_results"] += 1
            attempted.add(company)
            print(f"[{n}/{len(companies)}] {company[:50]} -> no results", flush=True)
        else:
            blob = " ".join(f"{r['title']} {r['content']}" for r in results)

            country = detect_country(blob, domain)
            size = detect_size(blob)

            # Confidence gate (F4): if the row's own domain TLD maps to a
            # country that disagrees with the web-search-detected country,
            # the generic-name query almost certainly matched a same-named
            # different company. Don't write the country and don't lock
            # `attempted` so a later run can retry — but keep size, since
            # size is less disambiguation-sensitive.
            tld_country = ""
            if domain and "." in domain:
                tld = domain.rsplit(".", 1)[-1].lower()
                tld_country = TLD_TO_COUNTRY.get(tld, "")
            country_conflict = bool(
                country and tld_country and country != tld_country
            )
            if country_conflict:
                stats["country_conflict"] += 1
                print(f"  [conflict] domain .{tld} -> {tld_country} vs "
                      f"detected {country}; not writing country for "
                      f"ambiguous name", file=sys.stderr)
                country = None

            applied_c = applied_s = 0
            for idx in company_rows[company]:
                row = rows[idx]
                cur_c = (row.get("Country / HQ") or "").strip().lower()
                cur_s = (row.get("Company Size") or "").strip().lower()
                if country and cur_c in STALE:
                    row["Country / HQ"] = country
                    applied_c += 1
                if size and cur_s in STALE:
                    row["Company Size"] = size
                    applied_s += 1

            if country:
                stats["country_hit"] += 1
            if size:
                stats["size_hit"] += 1
            if not country and not size and not country_conflict:
                stats["both_miss"] += 1
                attempted.add(company)
            elif country_conflict:
                # low-confidence: leave retryable, do not lock in attempted
                pass

            print(f"[{n}/{len(companies)}] {company[:45]:<45} "
                  f"C={country or '-'} S={size or '-'} "
                  f"(rows: c={applied_c} s={applied_s})", flush=True)

        if queries_done % args.save_every == 0:
            save()
        time.sleep(args.sleep)

    save()
    if args.attempted_log:
        try:
            with open(args.attempted_log, "w", encoding="utf-8") as f:
                json.dump(sorted(attempted), f, indent=2)
            print(f"  [saved attempted log] {args.attempted_log} ({len(attempted)})",
                  flush=True)
        except Exception as e:
            print(f"  [attempted-log write error] {e}", file=sys.stderr)
    print(f"\nDone. stats={dict(stats)}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
