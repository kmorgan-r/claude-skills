#!/usr/bin/env python3
"""
ClimatePoint Contact Intelligence — Tavily Enrichment

Reads the latest classified CSV, finds rows where Persona is empty, and
enriches Title / LinkedIn / Company / Summary / Headline via the Tavily
search API.

Preserves existing non-empty fields. Writes after every save_every rows
so a crash never loses work.

Usage:
    python tavily_enrich.py \
        --input  "C:/Users/kmorg/Downloads/climatepoint_contacts_FINAL_merged_classified_v12.csv" \
        --output "C:/Users/kmorg/Downloads/climatepoint_contacts_FINAL_merged_classified_v13.csv" \
        --tavily-key tvly-xxx \
        --limit 50 \
        --save-every 10
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

import requests


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


TAVILY_URL = "https://api.tavily.com/search"

LINKEDIN_RE = re.compile(r"https?://[a-z\.]*linkedin\.com/in/[^\s\"'?]+", re.IGNORECASE)

ROLE_WORDS = [
    "CEO", "CFO", "COO", "CTO", "CMO", "CSO", "Chief",
    "Founder", "Co-Founder", "Cofounder", "Owner", "President",
    "VP", "Vice President", "SVP", "EVP",
    "Director", "Managing Director", "Executive Director",
    "Head of", "Global Head", "Senior Head",
    "Partner", "Managing Partner", "General Partner", "Founding Partner",
    "Principal", "Lead", "Manager", "Senior Manager",
    "Investment Director", "Investment Manager", "Portfolio Manager",
    "Analyst", "Senior Analyst", "Associate", "Senior Associate",
    "Consultant", "Advisor", "Scientific Advisor",
    "Engineer", "Senior Engineer", "Scientist", "Researcher",
    "Sustainability Manager", "ESG Manager", "Sustainability Lead",
    "Marketing Manager", "Brand Manager", "Sales Manager",
    "Daglig leder", "Adm dir", "Norgessjef", "Leder", "Sjef",
]
ROLE_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in sorted(ROLE_WORDS, key=len, reverse=True)) + r")\b[^\n,|]{0,40}",
    re.IGNORECASE,
)
TITLE_STOP_RE = re.compile(r"\s+(at|@|–|—|-|in|focused|investing|leading|leads|leader|with|for|on|of)\s+", re.IGNORECASE)


def clean_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\ufffd", "").replace("\u200b", "")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def extract_title_from_snippet(snippet: str) -> Optional[str]:
    if not snippet:
        return None
    s = clean_text(snippet)
    m = ROLE_RE.search(s)
    if m:
        cand = clean_text(m.group(0))
        # cut at first stop word (at, in, of, focused, with, etc.)
        stop = TITLE_STOP_RE.search(cand)
        if stop:
            cand = cand[:stop.start()].strip()
        cand = cand.rstrip(".,;:- ")
        if 3 <= len(cand) <= 80:
            return cand
    return None


def first_line(s: str, max_len: int = 160) -> str:
    if not s:
        return ""
    for line in s.splitlines():
        line = clean_text(line)
        if len(line) >= 8:
            return line[:max_len]
    return clean_text(s)[:max_len]


def tavily_search(api_key: str, query: str, max_results: int = 5, timeout: int = 20) -> List[Dict[str, Any]]:
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": False,
    }
    try:
        r = requests.post(TAVILY_URL, json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json().get("results", []) or []
    except Exception as e:
        print(f"  [tavily error] {e}", file=sys.stderr)
        return []


def split_linkedin_title(title_str: str) -> Dict[str, str]:
    """
    Parse LinkedIn result titles like:
      "Jane Doe - VP Sustainability - Acme Corp | LinkedIn"
      "Jane Doe – Acme Corp | LinkedIn"
      "Jane Doe | LinkedIn"
    Returns dict with possible keys: person_name, role, company.
    """
    if not title_str:
        return {}
    s = title_str.replace(" | LinkedIn", "").replace(" - LinkedIn", "")
    s = s.replace(" – ", " - ").replace(" — ", " - ")
    parts = [p.strip() for p in s.split(" - ") if p.strip()]
    if not parts:
        return {}
    out = {"person_name": parts[0]}
    if len(parts) == 2:
        out["company"] = parts[1]
    elif len(parts) >= 3:
        out["role"] = parts[1]
        out["company"] = " - ".join(parts[2:])
    return out


def name_matches(first: str, last: str, candidate: str) -> bool:
    if not candidate:
        return False
    c = candidate.lower()
    f = (first or "").strip().lower()
    l = (last or "").strip().lower()
    if f and l:
        return f in c and l in c
    if l:
        return l in c
    if f:
        return f in c
    return False


def find_linkedin_url(results: List[Dict[str, Any]], first: str, last: str) -> Optional[Dict[str, str]]:
    for r in results:
        url = r.get("url", "") or ""
        if "linkedin.com/in/" not in url.lower():
            continue
        title = r.get("title", "") or ""
        if not name_matches(first, last, title):
            continue
        parsed = split_linkedin_title(title)
        parsed["linkedin"] = url.split("?")[0]
        parsed["snippet"] = (r.get("content", "") or "").strip()
        return parsed
    # Fallback: regex any linkedin URL in result content
    for r in results:
        content = r.get("content", "") or ""
        m = LINKEDIN_RE.search(content)
        if m and name_matches(first, last, r.get("title", "") + " " + content):
            return {"linkedin": m.group(0).split("?")[0],
                    "snippet": content.strip()}
    return None


def first_non_linkedin_summary(results: List[Dict[str, Any]], first: str, last: str) -> Optional[str]:
    for r in results:
        url = (r.get("url", "") or "").lower()
        if "linkedin.com" in url:
            continue
        content = (r.get("content", "") or "").strip()
        if not content:
            continue
        # Require some name presence to avoid wrong-person bios
        if name_matches(first, last, r.get("title", "") + " " + content):
            return content[:500]
    # Looser fallback — first useful content
    for r in results:
        url = (r.get("url", "") or "").lower()
        if "linkedin.com" in url:
            continue
        content = (r.get("content", "") or "").strip()
        if content:
            return content[:500]
    return None


def set_if_empty(row: Dict[str, str], field: str, value: Optional[str]) -> bool:
    if not value:
        return False
    cur = (row.get(field) or "").strip()
    if cur:
        return False
    row[field] = value.strip()
    return True


def build_query(first: str, last: str, company: str, domain: str) -> str:
    name = f"{first} {last}".strip()
    anchor = company.strip() or domain.strip()
    if name and anchor:
        return f'"{name}" {anchor} linkedin'
    if name:
        return f'"{name}" linkedin'
    return f"{anchor} contact"


def enrich_row(row: Dict[str, str], api_key: str) -> Dict[str, Any]:
    first = (row.get("First Name") or "").strip()
    last = (row.get("Last Name") or "").strip()
    company = (row.get("Company") or "").strip()
    domain = (row.get("Domain") or "").strip()

    if not (first or last) and not domain:
        return {"status": "skip", "reason": "no name or domain"}

    query = build_query(first, last, company, domain)
    results = tavily_search(api_key, query)
    if not results:
        return {"status": "no_results", "query": query}

    changed: List[str] = []

    li = find_linkedin_url(results, first, last)
    if li:
        snippet_clean = clean_text(li.get("snippet", ""))
        title_from_parse = li.get("role")
        title_from_snippet = extract_title_from_snippet(snippet_clean)
        title = title_from_parse or title_from_snippet
        li_company = li.get("company") or (row.get("Company") or "").strip()
        if title and li_company:
            headline = f"{title} at {li_company}"[:160]
        elif title:
            headline = title[:160]
        else:
            headline = first_line(li.get("snippet", ""), 160)
        full_name = f"{first} {last}".strip().lower()
        if headline and headline.strip().lower() == full_name:
            headline = ""

        if set_if_empty(row, "LinkedIn", li.get("linkedin")):
            changed.append("LinkedIn")
        if set_if_empty(row, "Title", title):
            changed.append("Title")
        if set_if_empty(row, "Company", li.get("company")):
            changed.append("Company")
        if set_if_empty(row, "Headline", headline):
            changed.append("Headline")
        if set_if_empty(row, "Summary", snippet_clean[:500]):
            changed.append("Summary")

    if not (row.get("Summary") or "").strip():
        s = first_non_linkedin_summary(results, first, last)
        s = clean_text(s or "")
        if set_if_empty(row, "Summary", s[:500]):
            changed.append("Summary")

    if not (row.get("Title") or "").strip():
        # last-ditch title from any result content
        for r in results:
            t = extract_title_from_snippet(r.get("content", "") or "")
            if t:
                if set_if_empty(row, "Title", t):
                    changed.append("Title")
                break

    return {"status": "ok", "query": query, "changed": changed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--tavily-key", default=os.getenv("TAVILY_API_KEY"))
    ap.add_argument("--limit", type=int, default=50, help="max rows to enrich this run")
    ap.add_argument("--save-every", type=int, default=10)
    ap.add_argument("--start-row", type=int, default=0, help="0-indexed data row to start scanning from")
    ap.add_argument("--sleep", type=float, default=0.3, help="delay between Tavily calls (sec)")
    ap.add_argument("--only-empty-persona", action="store_true", default=True)
    args = ap.parse_args()

    if not args.tavily_key:
        print("ERROR: --tavily-key or TAVILY_API_KEY required", file=sys.stderr)
        sys.exit(2)

    with open(args.input, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    print(f"loaded {len(rows)} rows from {args.input}")

    target_indices: List[int] = []
    for i, row in enumerate(rows):
        if i < args.start_row:
            continue
        persona = (row.get("Persona") or "").strip()
        email = (row.get("Email") or "").strip()
        domain = (row.get("Domain") or "").strip()
        title = (row.get("Title") or "").strip()
        if args.only_empty_persona and persona:
            continue
        if not email and not domain:
            continue
        if title and title.lower() not in ("unknown", "not found", "generic contact"):
            # already has title — still no persona, but classifier can handle
            # skip enrichment, leave for classifier step
            continue
        target_indices.append(i)
        if len(target_indices) >= args.limit:
            break

    print(f"will enrich {len(target_indices)} rows (limit={args.limit})")

    def flush():
        _atomic_write_csv(args.output, fieldnames, rows)

    stats = {"ok": 0, "no_results": 0, "skip": 0, "changed_fields": 0}
    t0 = time.time()
    for n, idx in enumerate(target_indices, start=1):
        row = rows[idx]
        first = row.get("First Name", "")
        last = row.get("Last Name", "")
        domain = row.get("Domain", "")
        print(f"[{n}/{len(target_indices)}] row {idx}: {first} {last} ({domain})")
        try:
            res = enrich_row(row, args.tavily_key)
        except Exception as e:
            print(f"  [error] {e}", file=sys.stderr)
            res = {"status": "skip", "reason": str(e)}
        status = res.get("status", "skip")
        stats[status] = stats.get(status, 0) + 1
        changed = res.get("changed", [])
        if changed:
            stats["changed_fields"] += len(changed)
            print(f"  -> {', '.join(changed)}")
        else:
            print(f"  -> no changes ({status})")
        if n % args.save_every == 0:
            flush()
            print(f"  [saved] {args.output}")
        time.sleep(args.sleep)

    flush()
    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s. stats={stats}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
