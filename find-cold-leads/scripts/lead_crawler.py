#!/usr/bin/env python3
"""find-cold-leads plumbing.

This module is deliberately NOT the brains of the skill. The qualification
judgment (Stage Q) and the contact/compliance mapping (Stage C) are performed by
Claude following SKILL.md. This script does the deterministic, repeatable work that
should never depend on a model:

  - Mode O discovery: run intent-signal searches, drop blocked vendor/directory
    domains, dedup by registrable domain, fetch a candidate's own pages.
  - The SINGLE shared writer: take qualified rows (from Mode A Apollo OR Mode O)
    and emit the Excel workbook + the classifier-ready CSV in one schema.

Everything here is pure/testable. Network calls are isolated in a few small
functions so the rest can be unit-tested offline.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

DEFAULT_OUTPUT = "./outputs/find-cold-leads.xlsx"
DEFAULT_CREDIT_BUDGET = 25

# ---------------------------------------------------------------------------
# Schema — the handoff contract with climatepoint-contact-intelligence.
# These are the EXACT column names the classifier's ensure_headers() expects.
# Extra columns we add are preserved by the classifier's set_if_empty pass.
# ---------------------------------------------------------------------------

# Identity columns the classifier reads for a contact even though they are not in
# ensure_headers (Email is read for identity; name columns are conventional).
IDENTITY_COLUMNS = ["Name", "First Name", "Last Name", "Email"]

# Verbatim from climatepoint_classifier.py ensure_headers().
CLASSIFIER_COLUMNS = [
    "Domain", "Title", "LinkedIn", "Company", "Summary", "Headline",
    "Department / Function", "Seniority", "Persona", "Lead Score (1-10)",
    "Need State", "Opportunity Type", "Outreach Angle", "Next Action",
    "Company Name", "Website", "Industry", "Company Size",
    "Revenue / Funding Stage", "Country / HQ", "Product Type",
    "Sustainability Claims", "Regulatory Exposure", "Has Physical Product",
    "Has Manufacturing / Supply Chain", "Has Investors / Portfolio",
    "Existing ESG Content", "Likely LCA Need", "Estimated Urgency",
    "Recommended Offer",
]

# Discovery/qualification/compliance columns this skill owns. The classifier
# ignores these but they keep the evidence + compliance posture auditable.
SKILL_COLUMNS = [
    "qualification_tier", "intent_signal", "business_relevance_basis",
    "evidence_snippet", "source_url", "source_mode", "contact_source",
    "region", "consent_status", "outreach_allowed_review",
    "legitimate_interest_basis", "email_verification_status",
    "linkedin_reference_url", "apollo_person_id", "apollo_credits_consumed",
]

LEADS_COLUMNS = IDENTITY_COLUMNS + CLASSIFIER_COLUMNS + SKILL_COLUMNS

# ---------------------------------------------------------------------------
# Domains: registrable domain (eTLD+1) + blocklist. Matching is on the HOST's
# registrable domain only — never a substring of the URL and never the company
# NAME. That is what lets "Apollo Tyres" (apollotyres.com) pass while the data
# vendor apollo.io is blocked.
# ---------------------------------------------------------------------------

# Common multi-label public suffixes so eTLD+1 is correct for e.g. co.uk.
MULTI_LABEL_SUFFIXES = {
    "co.uk", "org.uk", "gov.uk", "ac.uk", "ltd.uk", "plc.uk", "me.uk",
    "com.au", "net.au", "org.au", "co.nz", "co.za", "co.jp", "or.jp",
    "com.br", "com.mx", "com.tr", "com.cn", "com.sg", "com.hk", "co.in",
    "com.es", "com.pl", "com.ua",
}

# Data vendors, directories, aggregators, social, encyclopedias, generic news.
# Stored as registrable domains. is_blocked() compares the host's eTLD+1 to this
# set, so subdomains (de.zoominfo.com) and case variants are caught, while a
# token appearing only in a URL path is not.
BLOCKLIST = {
    # people-data vendors / contact databases
    "zoominfo.com", "lusha.com", "apollo.io", "rocketreach.co", "ensun.io",
    "cognism.com", "seamless.ai", "leadiq.com", "uplead.com", "adapt.io",
    "signalhire.com", "contactout.com", "snov.io", "hunter.io",
    # company directories / listicles / b2b portals
    "kompass.com", "europages.com", "europages.co.uk", "crunchbase.com",
    "dnb.com", "yellowpages.com", "yelp.com", "glassdoor.com", "indeed.com",
    "wlw.de", "thomasnet.com", "tradeindia.com", "alibaba.com",
    # social / encyclopedias
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "youtube.com", "wikipedia.org", "wikidata.org", "reddit.com",
    "pinterest.com", "tiktok.com", "medium.com",
    # generic news (intent should come from the company, not press coverage)
    "bloomberg.com", "reuters.com", "forbes.com", "businesswire.com",
    "prnewswire.com", "globenewswire.com",
}


def _host(url_or_host: str) -> str:
    s = (url_or_host or "").strip().lower()
    if "//" not in s:
        # bare host or host/path
        s = "http://" + s
    netloc = urllib.parse.urlsplit(s).netloc
    netloc = netloc.split("@")[-1].split(":")[0]  # drop creds + port
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def registrable_domain(url_or_host: str) -> str:
    """Return the eTLD+1 (registrable domain) for a URL or host, lowercased."""
    host = _host(url_or_host)
    if not host or "." not in host:
        return host
    parts = host.split(".")
    last2 = ".".join(parts[-2:])
    if len(parts) >= 3 and last2 in MULTI_LABEL_SUFFIXES:
        return ".".join(parts[-3:])
    return last2


def is_blocked(url_or_host: str) -> bool:
    """True if the host's registrable domain is a known vendor/directory."""
    return registrable_domain(url_or_host) in BLOCKLIST


def dedupe_by_domain(rows: Iterable[dict[str, Any]], key: str = "domain") -> list[dict[str, Any]]:
    """Keep the first row per registrable domain; preserve order."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        raw = row.get(key) or row.get("website") or row.get("source_url") or ""
        dom = registrable_domain(raw)
        if not dom or dom in seen:
            continue
        seen.add(dom)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Company-name sanity. The #1 historical failure was storing a SERP/blog TITLE as
# a company name ("The production of textile fabrics ...: tradition ...Storchen-
# wiege GmbH"). A real company name is short, has no sentence punctuation, and any
# legal suffix sits at the END, not buried mid-phrase.
# ---------------------------------------------------------------------------

_ENTITY_SUFFIX = re.compile(
    r"\b(gmbh|ag|kg|ug|se|ltd|limited|llc|inc|corp|plc|bv|nv|sa|spa|srl|"
    r"oy|ab|as|aps|sas|sarl|pty)\b",
    re.IGNORECASE,
)


def looks_like_bad_company_name(name: str) -> bool:
    """Heuristic guard: True if `name` looks like a SERP title, not a company."""
    if not name or not name.strip():
        return True
    n = name.strip()
    if "…" in n or "..." in n:
        return True
    if len(n) > 80:
        return True
    if ":" in n or " | " in n or " — " in n or " - " in n:
        return True
    words = n.split()
    if len(words) > 8:
        return True
    # Listicle / directory titles ("Top 100 ... Companies in Germany (2026)").
    # A single company's name rarely contains these tokens or a parenthesised year.
    if re.search(r"\b(companies|list|ranking|directory|top\s+\d+|best\s+\d+)\b", n, re.IGNORECASE):
        return True
    if re.search(r"\(?(19|20)\d{2}\)?", n):
        return True
    # A legal suffix that is NOT at/near the end => the name is a phrase that
    # happens to contain a company, e.g. "...innovation and Storchenwiege GmbH".
    matches = list(_ENTITY_SUFFIX.finditer(n))
    if matches:
        last = matches[-1]
        tail = n[last.end():].strip(" .,&")
        tail_words = [w for w in tail.split() if w and not _ENTITY_SUFFIX.fullmatch(w.strip(".,&"))]
        if len(tail_words) > 2:  # allow short tails like "& Co. KG"
            return True
    return False


# ---------------------------------------------------------------------------
# Region + compliance. Region is derived from the SEARCH location filter (a free,
# search-time input), NOT from Apollo's enrich-only `country`. So every row —
# including never-enriched ones — gets the right posture.
# ---------------------------------------------------------------------------

EU_COUNTRIES = {
    "germany", "france", "netherlands", "italy", "spain", "portugal", "belgium",
    "austria", "poland", "sweden", "denmark", "finland", "ireland", "greece",
    "czechia", "czech republic", "hungary", "romania", "bulgaria", "croatia",
    "slovakia", "slovenia", "estonia", "latvia", "lithuania", "luxembourg",
    "malta", "cyprus", "norway", "iceland", "liechtenstein",  # EEA
}
UK_NAMES = {"united kingdom", "uk", "great britain", "england", "scotland", "wales"}
US_NAMES = {"united states", "usa", "us", "u.s.", "united states of america", "america"}
STRICT_EU = {"germany"}  # UWG-strict (consent-leaning even B2B)

# ISO 3166-1 alpha-2 codes and common endonyms -> the canonical English name used
# in the sets above. A user (or a downstream Apollo location field) may pass "DE",
# "Deutschland", or "Munich Germany" rather than "Germany" — without these, those
# fall through to region=unknown/strict=False, i.e. Germany silently loses its
# UWG-strict posture. The failure direction (permissive) is exactly the wrong one
# for a compliance guard, so normalise aggressively.
COUNTRY_ALIASES = {
    # ISO alpha-2 (EU/EEA)
    "de": "germany", "fr": "france", "nl": "netherlands", "it": "italy",
    "es": "spain", "pt": "portugal", "be": "belgium", "at": "austria",
    "pl": "poland", "se": "sweden", "dk": "denmark", "fi": "finland",
    "ie": "ireland", "gr": "greece", "cz": "czechia", "hu": "hungary",
    "ro": "romania", "bg": "bulgaria", "hr": "croatia", "sk": "slovakia",
    "si": "slovenia", "ee": "estonia", "lv": "latvia", "lt": "lithuania",
    "lu": "luxembourg", "mt": "malta", "cy": "cyprus", "no": "norway",
    "is": "iceland", "li": "liechtenstein",
    # ISO alpha-2 (UK / US)
    "gb": "united kingdom", "uk": "united kingdom",
    "us": "united states", "usa": "united states",
    # endonyms / other-language exonyms
    "deutschland": "germany", "allemagne": "germany", "alemania": "germany",
    "frankreich": "france", "österreich": "austria", "oesterreich": "austria",
    "españa": "spain", "espana": "spain", "italia": "italy",
    "nederland": "netherlands", "sverige": "sweden", "danmark": "denmark",
    "suomi": "finland", "polska": "poland",
}


def _classify_country(name: str) -> dict[str, Any] | None:
    """Return a region descriptor for a single canonical country token, or None."""
    n = COUNTRY_ALIASES.get(name, name)
    if n in US_NAMES:
        return {"region": "US", "country": "United States", "strict": False}
    if n in UK_NAMES:
        return {"region": "UK", "country": "United Kingdom", "strict": False}
    if n in EU_COUNTRIES:
        return {"region": "EU", "country": n.title(), "strict": n in STRICT_EU}
    return None


def region_for_location(location: str | None) -> dict[str, Any]:
    """Map a location filter string to a compliance region descriptor."""
    loc = (location or "").strip().lower()
    if not loc or loc in {"european union", "eu", "eea", "europe"}:
        # An EU-wide or empty filter => conservative EU posture, no single country.
        return {"region": "EU", "country": "", "strict": False}
    # Pick the first recognised country mentioned. Check each delimited segment
    # whole first (so multi-word names like "united kingdom"/"czech republic"
    # survive), then each whitespace word inside it (so "Munich Germany" and a
    # bare "DE" both resolve). Whole-segment first keeps multi-word names intact.
    for segment in re.split(r"[,/;]| and | or ", loc):
        seg = segment.strip()
        if not seg:
            continue
        hit = _classify_country(seg)
        if hit:
            return hit
        # Word-level scan resolves "Munich Germany" and bare ISO codes. NB: a few
        # ISO aliases ("at", "be", "is", "no") are also common English words, so
        # this expects a location FILTER (e.g. "Munich, Germany"), not free prose —
        # feeding it a sentence could misfire on one of those tokens.
        for word in seg.split():
            hit = _classify_country(word)
            if hit:
                return hit
    return {"region": "unknown", "country": "", "strict": False}


def compliance_fields(region: dict[str, Any]) -> dict[str, str]:
    """Region-aware compliance posture. Unknown defaults to conservative EU."""
    r = region.get("region", "unknown")
    if r == "US":
        return {
            "consent_status": "n/a (opt-out)",
            "outreach_allowed_review": "ok with working unsubscribe + sender ID (CAN-SPAM)",
            "legitimate_interest_basis": "",
        }
    # EU / UK / unknown -> conservative.
    basis = (
        "B2B sustainability-software relevance to the contact's role; "
        "Art.14 source notice + opt-out required."
    )
    if region.get("strict"):
        basis = "STRICT (UWG §7 — express consent generally required even B2B). " + basis
    return {
        "consent_status": "unknown",
        "outreach_allowed_review": "needs review",
        "legitimate_interest_basis": basis,
    }


# ---------------------------------------------------------------------------
# Apollo -> handoff mapping. Two domains, two purposes:
#   - outreach `Domain`  = the EMAIL's domain (where you actually send)
#   - company identity    = organization.primary_domain (dedup / Website / blocklist)
# These can differ (sun-garden.de email vs sun-garden.eu org).
# ---------------------------------------------------------------------------


def email_domain(email: str | None) -> str:
    if email and "@" in email:
        return email.split("@", 1)[1].strip().lower()
    return ""


def map_apollo_person(enriched: dict[str, Any], region: dict[str, Any]) -> dict[str, Any]:
    """Map an apollo_people_match record to a Leads row (Stage C field mapping)."""
    org = enriched.get("organization") or {}
    email = enriched.get("email") or ""
    primary_domain = (org.get("primary_domain") or "").lower()
    out_domain = email_domain(email) or primary_domain
    first = enriched.get("first_name") or ""
    last = enriched.get("last_name") or ""
    name = enriched.get("name") or (f"{first} {last}".strip())
    matched = bool(email or enriched.get("id"))

    row = {c: "" for c in LEADS_COLUMNS}
    row.update({
        "Name": name,
        "First Name": first,
        "Last Name": last,
        "Email": email,
        "Domain": out_domain,
        "Title": enriched.get("title") or "",
        "LinkedIn": enriched.get("linkedin_url") or "",
        "Company": org.get("name") or "",
        "Company Name": org.get("name") or "",
        "Website": org.get("website_url") or (f"https://{primary_domain}" if primary_domain else ""),
        "Summary": org.get("short_description") or "",
        "Headline": enriched.get("headline") or "",
        "Seniority": enriched.get("seniority") or "",
        "Industry": org.get("industry") or "",
        "Company Size": str(org.get("estimated_num_employees") or ""),
        "Country / HQ": enriched.get("country") or region.get("country") or "",
        "source_mode": "apollo",
        "contact_source": "apollo_enriched" if email else "apollo_free",
        "region": region.get("region", "unknown"),
        "email_verification_status": enriched.get("email_status") or ("none" if not email else ""),
        "linkedin_reference_url": enriched.get("linkedin_url") or "",
        "apollo_person_id": enriched.get("id") or "",
        # Per-row REPORTING field: 1 iff this row is a revealed, paid enrich
        # (equivalently contact_source == "apollo_enriched"). It deliberately
        # returns 0 for an id-bearing-but-email-less record, because that is how
        # BOTH a never-paid free-search row AND a budget-exhausted "apollo_free"
        # fallback row arrive here — counting them would phantom-charge the run
        # total. This is distinct from the model's LIVE credits_spent counter
        # (SKILL.md credit gate), which counts on match to gate overspend; the
        # two have opposite safe directions, so they are intentionally not equal.
        "apollo_credits_consumed": 1 if (matched and email) else 0,
    })
    row.update(compliance_fields(region))
    return row


def contact_provenance_ok(contact_source_url: str, company_domain: str) -> bool:
    """A contact's evidence must come from the company's OWN registrable domain
    and never from a blocklisted vendor (the old ZoomInfo-snippet failure)."""
    if not contact_source_url or not company_domain:
        return False
    if is_blocked(contact_source_url):
        return False
    return registrable_domain(contact_source_url) == registrable_domain(company_domain)


# ---------------------------------------------------------------------------
# Themes + intent-signal query templates (Mode O). The point is to query for
# INTENT (companies already publishing EPDs / mentioning standards), not generic
# "textile manufacturer germany" which returns listicles.
# ---------------------------------------------------------------------------

INTENT_SIGNALS = [
    '"ISO 14067"', '"PEFCR"', '"product carbon footprint"', '"EPD"',
    '"environmental product declaration"', '"Digital Product Passport"',
    '"life cycle assessment"', '"sustainability report"',
]

PREBUILT_THEMES: dict[str, dict[str, Any]] = {
    "dpp-rollout-sectors": {
        "label": "DPP rollout sectors",
        "sectors": ["textile manufacturer", "apparel manufacturer",
                    "footwear manufacturer", "furniture manufacturer",
                    "mattress manufacturer", "toy manufacturer"],
    },
    "eu-taxonomy-lca": {
        "label": "EU Taxonomy / LCA opportunities",
        "sectors": ["manufacturer", "industrial manufacturer",
                    "consumer goods manufacturer"],
    },
    "standards-triggered-prospects": {
        "label": "Standards-triggered prospects",
        "sectors": ["manufacturer", "supplier"],
    },
}


def load_theme(theme_id: str | None, custom_file: str | None) -> tuple[str, dict[str, Any]]:
    if custom_file:
        data = json.loads(Path(custom_file).read_text(encoding="utf-8"))
        return data.get("id", "custom"), data
    if theme_id and theme_id in PREBUILT_THEMES:
        return theme_id, PREBUILT_THEMES[theme_id]
    return "dpp-rollout-sectors", PREBUILT_THEMES["dpp-rollout-sectors"]


def expand_queries(theme: dict[str, Any], location: str, max_queries: int = 12) -> list[str]:
    """Cross intent signals with sectors + location. Intent first, not generic."""
    sectors = theme.get("sectors") or ["manufacturer"]
    loc = (location or "").strip()
    queries: list[str] = []
    for sector in sectors:
        for signal in INTENT_SIGNALS:
            q = f'{signal} "{sector}"'
            if loc:
                q += f' "{loc}"'
            queries.append(q)
            if len(queries) >= max_queries:
                return queries
    return queries


# ---------------------------------------------------------------------------
# Search providers (Mode O). Kept minimal; only used for discovery. The result
# shape is normalised to {title, url, snippet}.
# ---------------------------------------------------------------------------

SEARCH_PROVIDERS = {
    "serper": "SERPER_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "fixture": None,
}


def _http_post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (trusted endpoints)
        return json.loads(resp.read().decode("utf-8"))


def serper_search(query: str, api_key: str, max_results: int) -> list[dict[str, Any]]:
    body = _http_post_json(
        "https://google.serper.dev/search",
        {"q": query, "num": max_results},
        {"X-API-KEY": api_key, "Content-Type": "application/json"},
    )
    return [
        {"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")}
        for r in body.get("organic", [])[:max_results]
    ]


def tavily_search(query: str, api_key: str, max_results: int) -> list[dict[str, Any]]:
    body = _http_post_json(
        "https://api.tavily.com/search",
        {"api_key": api_key, "query": query, "max_results": max_results},
        {"Content-Type": "application/json"},
    )
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in body.get("results", [])[:max_results]
    ]


def read_fixture(path: str) -> list[dict[str, Any]]:
    """Offline search results. Accepts SerpApi-style or {title,url,snippet} lists."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = data.get("organic_results") or data.get("organic") or data.get("results") or data
    out = []
    for r in raw:
        out.append({
            "title": r.get("title", ""),
            "url": r.get("url") or r.get("link", ""),
            "snippet": r.get("snippet") or r.get("content", ""),
        })
    return out


def run_search(query: str, provider: str, api_key: str | None, max_results: int) -> list[dict[str, Any]]:
    if provider == "serper":
        return serper_search(query, api_key or "", max_results)
    if provider == "tavily":
        return tavily_search(query, api_key or "", max_results)
    raise ValueError(f"Unknown or non-live provider: {provider}")


# ---------------------------------------------------------------------------
# Mode O discovery: search -> blocklist -> dedup. Produces CANDIDATES, not leads.
# Qualification (Stage Q) is Claude's job; this just clears obvious junk.
# ---------------------------------------------------------------------------


def discover_candidates(
    queries: list[str],
    provider: str,
    api_key: str | None,
    max_results: int,
    fixture: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (candidates, sources, rejected)."""
    candidates: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for query in queries:
        results = read_fixture(fixture) if fixture else run_search(query, provider, api_key, max_results)
        sources.append({"query": query, "result_count": len(results),
                        "source": "fixture" if fixture else provider})
        for r in results:
            url = r.get("url", "")
            if not url:
                continue
            if is_blocked(url):
                rejected.append({"name": r.get("title", ""), "domain": registrable_domain(url),
                                 "reason": "blocked vendor/directory domain", "source_url": url})
                continue
            candidates.append({
                "domain": registrable_domain(url),
                "website": f"https://{registrable_domain(url)}",
                "source_url": url,
                "title": r.get("title", ""),
                "snippet": r.get("snippet", ""),
                "source_mode": "open_web",
            })
        if fixture:
            break  # a fixture is a single canned result set
    candidates = dedupe_by_domain(candidates)
    return candidates, sources, rejected


def fetch_text(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (find-cold-leads)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read(500_000)
        return raw.decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 — fetch failure is flagged, not fatal
        return ""


# ---------------------------------------------------------------------------
# The single shared writer. Both Mode A (Apollo rows assembled by Claude) and
# Mode O feed through here so the schema stays identical.
# ---------------------------------------------------------------------------


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    out = {c: "" for c in LEADS_COLUMNS}
    for k, v in row.items():
        if k in out:
            out[k] = "" if v is None else v
    return out


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Classifier-ready CSV (utf-8-sig so Excel on Windows shows accents)."""
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LEADS_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(_normalize_row(row))


def write_workbook(
    path: Path,
    leads: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    run_config: dict[str, Any],
) -> Path:
    try:
        import openpyxl  # noqa: PLC0415
    except ImportError:
        print("[warn] openpyxl not installed; writing CSV only.", file=sys.stderr)
        return path
    wb = openpyxl.Workbook()

    ws = wb.active
    ws.title = "Leads"
    ws.append(LEADS_COLUMNS)
    for row in leads:
        n = _normalize_row(row)
        ws.append([n[c] for c in LEADS_COLUMNS])

    ws_src = wb.create_sheet("Sources")
    ws_src.append(["query", "result_count", "source"])
    for s in sources:
        ws_src.append([s.get("query", ""), s.get("result_count", ""), s.get("source", "")])

    ws_rej = wb.create_sheet("Rejected")
    ws_rej.append(["name", "domain", "reason", "source_url"])
    for r in rejected:
        ws_rej.append([r.get("name", ""), r.get("domain", ""), r.get("reason", ""), r.get("source_url", "")])

    ws_cfg = wb.create_sheet("Run Config")
    ws_cfg.append(["key", "value"])
    for k, v in run_config.items():
        ws_cfg.append([k, str(v)])

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def write_leads(
    leads: list[dict[str, Any]],
    output: str,
    sources: list[dict[str, Any]] | None = None,
    rejected: list[dict[str, Any]] | None = None,
    run_config: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Write the Excel workbook + classifier-ready CSV. Returns the paths."""
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path = out_path.with_suffix(".csv")
    write_csv(csv_path, leads)
    write_workbook(out_path, leads, sources or [], rejected or [], run_config or {})
    return {"xlsx": str(out_path), "csv": str(csv_path)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="find-cold-leads plumbing (Mode O discovery + shared writer).")
    p.add_argument("--mode", choices=["open_web"], default="open_web",
                   help="Mode A (Apollo) is driven from SKILL.md; the script handles Mode O + writing.")
    p.add_argument("--theme", choices=sorted(PREBUILT_THEMES))
    p.add_argument("--custom-theme-file")
    p.add_argument("--location", default="European Union")
    p.add_argument("--max-queries", type=int, default=12)
    p.add_argument("--max-results", type=int, default=10)
    p.add_argument("--search-provider", choices=sorted(SEARCH_PROVIDERS), default="serper")
    p.add_argument("--search-api-key")
    p.add_argument("--fixture", help="Offline search results JSON (no network).")
    p.add_argument("--no-crawl-pages", action="store_true")
    p.add_argument("--write-leads", action="store_true",
                   help="Write leads from a candidates JSON (rows already in handoff schema).")
    p.add_argument("--candidates", help="JSON file: {leads:[...], sources:[...], rejected:[...], run_config:{}}")
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--credit-budget", type=int, default=DEFAULT_CREDIT_BUDGET)
    p.add_argument("--list-themes", action="store_true")
    p.add_argument("--list-providers", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.list_themes:
        for tid, t in PREBUILT_THEMES.items():
            print(f"{tid}: {t['label']}")
        return 0
    if args.list_providers:
        for name, env in SEARCH_PROVIDERS.items():
            print(f"{name}: {'no key (offline)' if env is None else env}")
        return 0

    if args.write_leads:
        if not args.candidates:
            print("--write-leads requires --candidates <json>", file=sys.stderr)
            return 2
        payload = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
        paths = write_leads(
            payload.get("leads", []), args.output,
            payload.get("sources"), payload.get("rejected"), payload.get("run_config"),
        )
        print(json.dumps(paths, indent=2))
        return 0

    # Mode O discovery
    theme_id, theme = load_theme(args.theme, args.custom_theme_file)
    region = region_for_location(args.location)
    queries = expand_queries(theme, args.location, args.max_queries)
    api_key = args.search_api_key or (os.environ.get(SEARCH_PROVIDERS.get(args.search_provider) or "") or None)
    candidates, sources, rejected = discover_candidates(
        queries, args.search_provider, api_key, args.max_results, args.fixture,
    )

    region_desc = region_for_location(args.location)
    run_config = {
        "theme_id": theme_id, "theme_label": theme.get("label", ""),
        "location": args.location, "region": region_desc.get("region"),
        "country": region_desc.get("country"), "strict": region_desc.get("strict"),
        "source_mode": "open_web", "search_provider": args.search_provider,
        "credit_budget": args.credit_budget,
        "searched": len(sources), "candidates": len(candidates), "blocked": len(rejected),
        "note": "Mode O candidates are NOT qualified leads. Run Stage Q (SKILL.md) "
                "before treating any row as a lead.",
    }
    # Mode O emits candidates as provisional Leads rows for Claude to qualify;
    # they carry no contact/compliance until Stage Q/C run.
    leads = []
    for c in candidates:
        row = {col: "" for col in LEADS_COLUMNS}
        row.update({
            "Domain": c["domain"], "Website": c["website"],
            "source_url": c["source_url"], "evidence_snippet": c.get("snippet", ""),
            "source_mode": "open_web", "contact_source": "open_web",
            "region": region.get("region"),
        })
        row.update(compliance_fields(region))
        leads.append(row)

    paths = write_leads(leads, args.output, sources, rejected, run_config)
    print(json.dumps({"paths": paths, **{k: run_config[k] for k in ("searched", "candidates", "blocked")}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
