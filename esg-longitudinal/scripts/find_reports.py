#!/usr/bin/env python3
"""Find candidate free report PDFs for a company across years.

Strategy: query DuckDuckGo (no API key) for sustainability / annual report PDFs.
If the `ddgs` package isn't installed, print the exact queries so the calling
agent can run them with its own web-search tool instead.

Usage:
    python find_reports.py --company "Royal Philips" --years 2015-2024
    python find_reports.py --company "Philips" --years 2015,2018,2020 --domain-hint "circular economy"
"""
import argparse
import json


def parse_years(s):
    out = set()
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return sorted(out)


def get_ddgs():
    try:
        from ddgs import DDGS  # current package name
        return DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # older name
            return DDGS
        except ImportError:
            return None


def search(DDGS, query, max_results):
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            url = r.get("href") or r.get("link") or ""
            results.append({"title": r.get("title", ""), "url": url})
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True)
    ap.add_argument("--years", required=True, help="e.g. 2015-2024 or 2018,2020")
    ap.add_argument("--max-per-year", type=int, default=4)
    ap.add_argument("--domain-hint", default="", help="optional, e.g. 'circular economy'")
    args = ap.parse_args()

    years = parse_years(args.years)
    hint = f" {args.domain_hint}" if args.domain_hint else ""
    queries = []
    for y in years:
        queries.append(f'"{args.company}" sustainability report {y} filetype:pdf')
        queries.append(f'"{args.company}" annual report {y}{hint} filetype:pdf')

    DDGS = get_ddgs()
    if DDGS is None:
        print(json.dumps({
            "ddgs_available": False,
            "note": "pip install ddgs  — OR run these queries with your own web-search tool.",
            "queries": queries,
            "results": [],
        }, indent=2))
        return

    found = []
    for y in years:
        for q in (f'"{args.company}" sustainability report {y} filetype:pdf',
                  f'"{args.company}" annual report {y}{hint} filetype:pdf'):
            for r in search(DDGS, q, args.max_per_year):
                if ".pdf" in r["url"].lower():
                    found.append({"year": y, "title": r["title"], "url": r["url"]})

    seen, uniq = set(), []
    for f in found:
        if f["url"] not in seen:
            seen.add(f["url"])
            uniq.append(f)

    print(json.dumps({
        "ddgs_available": True,
        "company": args.company,
        "years": years,
        "results": uniq,
    }, indent=2))


if __name__ == "__main__":
    main()
