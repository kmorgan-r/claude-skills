#!/usr/bin/env python3
"""
Brave Search port of enrich_recover.py / serper_recover.py.

Brave Search API:
  GET https://api.search.brave.com/res/v1/web/search
  Header: X-Subscription-Token
  Response: { "web": { "results": [{title, url, description}, ...] } }

Free tier: 1 qps, 2000 queries/month. Default --sleep 1.1 honors rate limit.

Usage:
    python brave_recover.py \
        --input  v17b.csv \
        --output v18.csv \
        --brave-key BSA... \
        --row-min 0 --row-max 5666
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import os
import re
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

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


HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("te", os.path.join(HERE, "tavily_enrich.py"))
te = importlib.util.module_from_spec(spec)
spec.loader.exec_module(te)

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

GENERIC_LOCALS = {
    "info", "contact", "hello", "sales", "marketing", "admin", "team",
    "hi", "kontakt", "post", "kundeservice", "support", "office",
    "firmapost", "press", "media", "jobs", "careers", "hr", "noreply",
    "no-reply", "mail", "email", "enquiries", "general", "reception",
    "investors", "ir",
}

DOMAIN_TO_COMPANY = {
    "htgf.de": "High-Tech Gruenderfonds",
    "usv.com": "Union Square Ventures",
    "tikehaucapital.com": "Tikehau Capital",
    "advantagecap.com": "Advantage Capital",
    "cantor.com": "Cantor Fitzgerald",
    "ctinnovations.com": "Connecticut Innovations",
    "amazon.com": "Amazon Climate Pledge Fund",
    "vanguard.com": "Vanguard Group",
    "somv.com": "Sapphire Ventures",
    "valorep.com": "Valor Equity Partners",
    "alantecapital.com": "Alante Capital",
    "enertechcapital.com": "EnerTech Capital",
    "brookfield.com": "Brookfield Asset Management",
    "generalcatalyst.com": "General Catalyst",
    "warburgpincus.com": "Warburg Pincus",
    "energyinfrapartners.com": "Energy Infrastructure Partners",
}

EMAIL_LOCAL_RE = re.compile(r"^([^@]+)@", re.IGNORECASE)


def brave_search(api_key: str, query: str, linkedin_only: bool,
                 max_results: int = 5, timeout: int = 20) -> List[Dict[str, Any]]:
    q = f"site:linkedin.com/in {query}" if linkedin_only else query
    headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}
    params = {"q": q, "count": max_results}
    try:
        r = requests.get(BRAVE_URL, headers=headers, params=params, timeout=timeout)
        if r.status_code == 429:
            # rate limited — back off and retry once
            time.sleep(2.0)
            r = requests.get(BRAVE_URL, headers=headers, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json() or {}
    except Exception as e:
        print(f"  [brave error] {e}", file=sys.stderr)
        return []
    out: List[Dict[str, Any]] = []
    for item in (data.get("web", {}) or {}).get("results", []) or []:
        out.append({
            "title": item.get("title", "") or "",
            "url": item.get("url", "") or "",
            "content": item.get("description", "") or "",
        })
    return out


def parse_email_name(email: str) -> Optional[Dict[str, str]]:
    m = EMAIL_LOCAL_RE.match((email or "").lower().strip())
    if not m:
        return None
    local = m.group(1)
    if local in GENERIC_LOCALS:
        return None
    parts = re.split(r"[._\-]", local)
    parts = [p for p in parts if p.isalpha() and len(p) >= 2]
    if len(parts) >= 2:
        return {"first": parts[0].capitalize(), "last": parts[-1].capitalize()}
    return None


def is_generic_inbox(email: str) -> bool:
    m = EMAIL_LOCAL_RE.match((email or "").lower().strip())
    if not m:
        return False
    local = m.group(1)
    if local in GENERIC_LOCALS:
        return True
    for g in GENERIC_LOCALS:
        if g == local or local.startswith(g + ".") or local.endswith("." + g):
            return True
    return False


def mark_generic(row: Dict[str, str]) -> None:
    te.set_if_empty(row, "Persona", "Generic Contact")
    te.set_if_empty(row, "Lead Score (1-10)", "1")
    te.set_if_empty(row, "Need State", "Not enough information")
    te.set_if_empty(row, "Opportunity Type", "None")
    te.set_if_empty(row, "Outreach Angle", "Generic inbox - no individual to address")
    te.set_if_empty(row, "Next Action", "Exclude - generic inbox")
    te.set_if_empty(row, "Seniority", "Unknown")


def enrich_with_params(row: Dict[str, str], first: str, last: str,
                       company: str, domain: str, api_key: str,
                       linkedin_only: bool) -> Dict[str, Any]:
    if not (first or last) and not domain:
        return {"status": "skip", "reason": "no name or domain"}

    name = f"{first} {last}".strip()
    anchor = company.strip() or DOMAIN_TO_COMPANY.get((domain or "").lower(), domain.strip())
    if name and anchor:
        query = f'"{name}" {anchor}'
    elif name:
        query = f'"{name}"'
    else:
        query = f"{anchor} contact"

    results = brave_search(api_key, query, linkedin_only=linkedin_only)
    if not results and linkedin_only:
        results = brave_search(api_key, query, linkedin_only=False)
    if not results:
        return {"status": "no_results", "query": query}

    changed: List[str] = []

    li = te.find_linkedin_url(results, first, last)
    if li:
        snippet_clean = te.clean_text(li.get("snippet", ""))
        title_from_parse = li.get("role")
        title_from_snippet = te.extract_title_from_snippet(snippet_clean)
        title = title_from_parse or title_from_snippet
        li_company = li.get("company") or company
        if title and li_company:
            headline = f"{title} at {li_company}"[:160]
        elif title:
            headline = title[:160]
        else:
            headline = te.first_line(li.get("snippet", ""), 160)
        full_name = name.lower()
        if headline and headline.strip().lower() == full_name:
            headline = ""

        if te.set_if_empty(row, "LinkedIn", li.get("linkedin")):
            changed.append("LinkedIn")
        if te.set_if_empty(row, "Title", title):
            changed.append("Title")
        if te.set_if_empty(row, "Company", li.get("company")):
            changed.append("Company")
        if te.set_if_empty(row, "Headline", headline):
            changed.append("Headline")
        if te.set_if_empty(row, "Summary", snippet_clean[:500]):
            changed.append("Summary")

    if not (row.get("Summary") or "").strip():
        s = te.first_non_linkedin_summary(results, first, last)
        s = te.clean_text(s or "")
        if te.set_if_empty(row, "Summary", s[:500]):
            changed.append("Summary")

    if not (row.get("Title") or "").strip():
        for r in results:
            t = te.extract_title_from_snippet(r.get("content", "") or "")
            if t:
                if te.set_if_empty(row, "Title", t):
                    changed.append("Title")
                break

    if first and not (row.get("First Name") or "").strip():
        row["First Name"] = first
        changed.append("First Name")
    if last and not (row.get("Last Name") or "").strip():
        row["Last Name"] = last
        changed.append("Last Name")

    return {"status": "ok", "query": query, "changed": changed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--brave-key", default=os.getenv("BRAVE_API_KEY"))
    ap.add_argument("--row-min", type=int, default=0)
    ap.add_argument("--row-max", type=int, default=10**9)
    ap.add_argument("--limit", type=int, default=10**9)
    ap.add_argument("--save-every", type=int, default=25)
    ap.add_argument("--sleep", type=float, default=1.1,
                    help="Brave free tier = 1 qps, default 1.1s honors limit")
    ap.add_argument("--skip-named", action="store_true")
    args = ap.parse_args()

    if not args.brave_key:
        sys.exit("Missing --brave-key or BRAVE_API_KEY")

    with open(args.input, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    stats = {"parseable_ok": 0, "parseable_fail": 0,
             "generic_marked": 0, "single_token_skip": 0,
             "named_ok": 0, "named_fail": 0}
    processed = 0

    def save():
        _atomic_write_csv(args.output, fieldnames, rows)
        print(f"  [saved] {args.output}", flush=True)

    for i, row in enumerate(rows):
        if i < args.row_min or i > args.row_max:
            continue
        if (row.get("Persona") or "").strip():
            continue
        if processed >= args.limit:
            break

        first = (row.get("First Name") or "").strip()
        last = (row.get("Last Name") or "").strip()
        email = (row.get("Email") or "").strip()
        domain = (row.get("Domain") or "").strip()
        company = (row.get("Company") or "").strip()

        nameless = not (first or last)

        if nameless:
            if is_generic_inbox(email):
                mark_generic(row)
                stats["generic_marked"] += 1
                processed += 1
                print(f"[{processed}] row {i}: generic inbox {email} -> marked", flush=True)
            else:
                parsed = parse_email_name(email)
                if not parsed:
                    stats["single_token_skip"] += 1
                    continue
                first2, last2 = parsed["first"], parsed["last"]
                print(f"[{processed+1}] row {i}: parseable {email} -> {first2} {last2}", flush=True)
                res = enrich_with_params(row, first2, last2, company, domain,
                                         args.brave_key, linkedin_only=False)
                if res["status"] == "ok" and res.get("changed"):
                    stats["parseable_ok"] += 1
                    print(f"  -> changed: {', '.join(res['changed'])}", flush=True)
                else:
                    stats["parseable_fail"] += 1
                    print(f"  -> no changes ({res['status']})", flush=True)
                processed += 1
                time.sleep(args.sleep)
        else:
            if args.skip_named:
                continue
            print(f"[{processed+1}] row {i}: requery {first} {last} ({domain})", flush=True)
            res = enrich_with_params(row, first, last, company, domain,
                                     args.brave_key, linkedin_only=True)
            if res["status"] == "ok" and res.get("changed"):
                stats["named_ok"] += 1
                print(f"  -> changed: {', '.join(res['changed'])}", flush=True)
            else:
                stats["named_fail"] += 1
                print(f"  -> no changes ({res['status']})", flush=True)
            processed += 1
            time.sleep(args.sleep)

        if processed % args.save_every == 0:
            save()

    save()
    print(f"\nDone. stats={stats}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
