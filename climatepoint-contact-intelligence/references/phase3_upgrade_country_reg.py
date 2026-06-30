#!/usr/bin/env python3
"""
Phase 3 follow-up: smarter Country / HQ + Regulatory Exposure heuristic.

Targets rows with Lead Score >= --min-score. Preserves all existing meaningful
values (not in {unknown, minimal, ""}). Updates Estimated Urgency when
Regulatory becomes meaningful.

Country sources, priority order:
  1. company name suffix (AS, GmbH, Ltd, Inc, BV, SARL, Oy, A/S, SpA, ...)
  2. city in Company+Summary+Headline
  3. country word in Company+Summary+Headline
  4. domain TLD (.no/.se/.de/.uk/...)

Regulatory rules (multiple can stack, joined with " + "):
  - Persona Investor/Fund                          -> SFDR
  - Industry Renewable Energy                      -> EU Taxonomy — Energy life-cycle GHG threshold (4.x)
  - Industry Technology / Software                 -> EU Taxonomy — Digital / ICT avoided-emissions LCA (8.2)
  - Industry Manufacturing / Industrial            -> EU Taxonomy — Product / manufacturing LCA (3.x)
  - Industry Food & Agriculture                    -> EU Taxonomy — Product / manufacturing LCA (3.x)
  - Industry Construction (incl. text: building,
      cement, steel, materials, infrastructure)    -> EPD opportunity
  - Industry Transportation/Mobility + battery/EV  -> EU Battery Regulation
  - Text: furniture, mattress, toy, textile,
      clothing, apparel, fashion, footwear         -> DPP (Digital Product Passport)
  - Text/Industry literal "csrd" or "eu listed"    -> CSRD
  - .uk domain + Investor                          -> UK climate disclosure
  - No rule fires                                  -> Minimal

Usage:
    python phase3_upgrade_country_reg.py \\
        --input  v21.csv \\
        --output v22.csv \\
        --min-score 7
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter


P3_FIELDS = [
    "Country / HQ",
    "Regulatory Exposure",
    "Estimated Urgency",
]

STALE = {"unknown", "none found", "minimal", "", "not enough information",
         "no", "n/a", "na"}


# ── COUNTRY ──────────────────────────────────────────────────────────────────

# Order matters: more-specific (longer, less-ambiguous) first.
COUNTRY_SUFFIXES = [
    # Norway
    (r"\bASA\b", "Norway"),
    (r"\bAS\b",  "Norway"),
    # Sweden
    (r"\bAB\b",  "Sweden"),
    # Germany
    (r"\bGmbH\b", "Germany"),
    (r"\bAG\b",   "Germany"),
    (r"\bKG\b",   "Germany"),
    # UK
    (r"\bPLC\b",  "United Kingdom"),
    (r"\bLLP\b",  "United Kingdom"),
    (r"\bLimited\b", "United Kingdom"),
    (r"\bLtd\.?\b",  "United Kingdom"),
    # US
    (r"\bInc\.?\b", "United States"),
    (r"\bLLC\b",    "United States"),
    (r"\bCorp\.?\b", "United States"),
    # Netherlands
    (r"\bB\.?V\.?\b", "Netherlands"),
    (r"\bN\.?V\.?\b", "Netherlands"),
    # France
    (r"\bSARL\b", "France"),
    (r"\bSAS\b",  "France"),
    # Finland
    (r"\bOyj\b", "Finland"),
    (r"\bOy\b",  "Finland"),
    # Denmark
    (r"\bA/S\b", "Denmark"),
    (r"\bApS\b", "Denmark"),
    # Italy
    (r"\bS\.?p\.?A\.?\b", "Italy"),
    (r"\bS\.?r\.?l\.?\b", "Italy"),
]

CITY_TO_COUNTRY = {
    # Nordic
    "Oslo": "Norway", "Bergen": "Norway", "Trondheim": "Norway",
    "Stavanger": "Norway", "Tromsø": "Norway", "Tromso": "Norway",
    "Stockholm": "Sweden", "Gothenburg": "Sweden", "Malmö": "Sweden",
    "Malmo": "Sweden", "Uppsala": "Sweden",
    "Copenhagen": "Denmark", "Aarhus": "Denmark",
    "Helsinki": "Finland", "Espoo": "Finland", "Tampere": "Finland",
    "Reykjavik": "Iceland", "Reykjavík": "Iceland",
    # DACH
    "Berlin": "Germany", "Munich": "Germany", "München": "Germany",
    "Hamburg": "Germany", "Frankfurt": "Germany", "Cologne": "Germany",
    "Köln": "Germany", "Stuttgart": "Germany", "Düsseldorf": "Germany",
    "Dusseldorf": "Germany",
    "Vienna": "Austria", "Wien": "Austria", "Graz": "Austria",
    "Zurich": "Switzerland", "Zürich": "Switzerland", "Geneva": "Switzerland",
    "Genève": "Switzerland", "Basel": "Switzerland", "Bern": "Switzerland",
    # UK / Ireland
    "London": "United Kingdom", "Manchester": "United Kingdom",
    "Edinburgh": "United Kingdom", "Cambridge": "United Kingdom",
    "Oxford": "United Kingdom", "Bristol": "United Kingdom",
    "Birmingham": "United Kingdom", "Glasgow": "United Kingdom",
    "Dublin": "Ireland", "Cork": "Ireland",
    # US
    "New York": "United States", "San Francisco": "United States",
    "Boston": "United States", "Chicago": "United States",
    "Los Angeles": "United States", "Seattle": "United States",
    "Austin": "United States", "Atlanta": "United States",
    "Denver": "United States", "Houston": "United States",
    "Dallas": "United States", "Miami": "United States",
    "Washington DC": "United States", "Palo Alto": "United States",
    "Cambridge MA": "United States",
    # BeNeLux + France
    "Amsterdam": "Netherlands", "Rotterdam": "Netherlands",
    "Utrecht": "Netherlands", "The Hague": "Netherlands",
    "Eindhoven": "Netherlands", "Delft": "Netherlands",
    "Brussels": "Belgium", "Antwerp": "Belgium", "Ghent": "Belgium",
    "Luxembourg": "Luxembourg",
    "Paris": "France", "Lyon": "France", "Marseille": "France",
    "Toulouse": "France", "Bordeaux": "France", "Nice": "France",
    # Iberia / Italy
    "Madrid": "Spain", "Barcelona": "Spain", "Valencia": "Spain",
    "Lisbon": "Portugal", "Porto": "Portugal",
    "Milan": "Italy", "Rome": "Italy", "Turin": "Italy",
    "Bologna": "Italy", "Florence": "Italy",
    # CEE
    "Warsaw": "Poland", "Kraków": "Poland", "Krakow": "Poland",
    "Prague": "Czech Republic", "Budapest": "Hungary",
    "Bucharest": "Romania", "Sofia": "Bulgaria",
    "Tallinn": "Estonia", "Riga": "Latvia", "Vilnius": "Lithuania",
    # Americas
    "Toronto": "Canada", "Vancouver": "Canada", "Montreal": "Canada",
    "São Paulo": "Brazil", "Sao Paulo": "Brazil",
    "Mexico City": "Mexico",
    # Asia / Oceania
    "Sydney": "Australia", "Melbourne": "Australia",
    "Auckland": "New Zealand",
    "Tokyo": "Japan", "Osaka": "Japan",
    "Singapore": "Singapore",
    "Hong Kong": "Hong Kong", "Shanghai": "China", "Beijing": "China",
    "Shenzhen": "China",
    "Mumbai": "India", "Bangalore": "India", "Bengaluru": "India",
    "Delhi": "India", "Hyderabad": "India",
    "Tel Aviv": "Israel", "Jerusalem": "Israel",
    "Cape Town": "South Africa", "Johannesburg": "South Africa",
    "Dubai": "UAE", "Abu Dhabi": "UAE",
}

COUNTRY_WORDS = [
    "Norway", "Sweden", "Denmark", "Finland", "Iceland",
    "Germany", "Austria", "Switzerland",
    "United Kingdom", "England", "Scotland", "Wales", "Ireland",
    "United States", "Canada",
    "Netherlands", "Belgium", "Luxembourg",
    "France", "Spain", "Portugal", "Italy",
    "Poland", "Czech Republic", "Hungary", "Romania",
    "Estonia", "Latvia", "Lithuania",
    "Australia", "New Zealand",
    "Japan", "Singapore", "China", "India",
    "Israel", "South Africa", "Brazil", "Mexico", "UAE",
]
# pre-compile country word patterns (whole word)
COUNTRY_WORD_PATTERNS = [(re.compile(rf"\b{re.escape(w)}\b"), w) for w in COUNTRY_WORDS]
# add a few padded shorthands
SHORT_WORDS = [(re.compile(r"\bUK\b"), "United Kingdom"),
               (re.compile(r"\bUSA\b"), "United States"),
               (re.compile(r"\bU\.S\.\b"), "United States")]
COUNTRY_WORD_PATTERNS = SHORT_WORDS + COUNTRY_WORD_PATTERNS

TLD_TO_COUNTRY = {
    "no": "Norway", "se": "Sweden", "dk": "Denmark", "fi": "Finland",
    "is": "Iceland",
    "de": "Germany", "at": "Austria", "ch": "Switzerland",
    "uk": "United Kingdom", "ie": "Ireland",
    "fr": "France", "es": "Spain", "pt": "Portugal", "it": "Italy",
    "nl": "Netherlands", "be": "Belgium", "lu": "Luxembourg",
    "pl": "Poland", "cz": "Czech Republic", "hu": "Hungary",
    "ee": "Estonia", "lv": "Latvia", "lt": "Lithuania",
    "au": "Australia", "nz": "New Zealand",
    "jp": "Japan", "sg": "Singapore", "cn": "China", "in": "India",
    "il": "Israel", "za": "South Africa",
    "br": "Brazil", "mx": "Mexico", "ca": "Canada", "ae": "UAE",
}


def detect_country(row):
    """Return (country, source) or (None, None)."""
    company  = (row.get("Company",  "") or "").strip()
    summary  = (row.get("Summary",  "") or "").strip()
    headline = (row.get("Headline", "") or "").strip()
    domain   = (row.get("Domain",   "") or "").strip().lower()
    text     = f"{company} {summary} {headline}"

    # 1. suffix on Company
    for pat, country in COUNTRY_SUFFIXES:
        if re.search(pat, company):
            return country, "suffix"

    # 2. city in text
    for city, country in CITY_TO_COUNTRY.items():
        if re.search(rf"\b{re.escape(city)}\b", text):
            return country, "city"

    # 3. country word in text
    for pat, country in COUNTRY_WORD_PATTERNS:
        if pat.search(text):
            return country, "word"

    # 4. TLD
    if domain and "." in domain:
        tld = domain.rsplit(".", 1)[-1]
        if tld in TLD_TO_COUNTRY:
            return TLD_TO_COUNTRY[tld], "tld"

    return None, None


# ── REGULATORY ───────────────────────────────────────────────────────────────

REG_SFDR     = "SFDR"
REG_DIGITAL  = "EU Taxonomy — Digital / ICT avoided-emissions LCA (8.2)"
REG_ENERGY   = "EU Taxonomy — Energy life-cycle GHG threshold (4.x)"
REG_PRODUCT  = "EU Taxonomy — Product / manufacturing LCA (3.x)"
REG_EPD      = "EPD opportunity"
REG_BATTERY  = "EU Battery Regulation"
REG_DPP      = "DPP (Digital Product Passport)"
REG_CSRD     = "CSRD"
REG_UK       = "UK climate disclosure"

CONSTRUCTION_KW = re.compile(
    r"\b(construction|building|buildings|cement|concrete|steel|"
    r"insulation|facade|façade|infrastructure|materials science|"
    r"prefab|modular building|architect|civil engineering)\b", re.I)
BATTERY_KW = re.compile(
    r"\b(battery|batteries|cell manufactur|li[\s-]ion|lithium[\s-]ion|"
    r"electric vehicle|ev fleet|charging infrastructure|ev battery|"
    r"second life batter)\b", re.I)
DPP_KW = re.compile(
    r"\b(furniture|mattress|mattresses|toy|toys|textile|textiles|"
    r"clothing|apparel|fashion|footwear|shoes|garment|sportswear)\b",
    re.I)


def derive_regulatory(row):
    """Return list of regulation labels (possibly empty)."""
    persona  = (row.get("Persona",  "") or "").strip()
    industry = (row.get("Industry", "") or "").strip().lower()
    company  = (row.get("Company",  "") or "").strip()
    summary  = (row.get("Summary",  "") or "").strip()
    headline = (row.get("Headline", "") or "").strip()
    domain   = (row.get("Domain",   "") or "").strip().lower()
    text = f"{company} {summary} {headline}".lower()
    text_cs = f"{company} {summary} {headline}"  # case-sensitive for some

    labels = []

    # Persona = Investor / Fund
    if "investor" in persona.lower() or "fund" in persona.lower():
        labels.append(REG_SFDR)

    # Industry-driven
    if "renewable energy" in industry or "energy" in industry and "venture" not in industry:
        if REG_ENERGY not in labels:
            labels.append(REG_ENERGY)

    if "technology" in industry or "software" in industry:
        if REG_DIGITAL not in labels:
            labels.append(REG_DIGITAL)

    if "manufacturing" in industry or "industrial" in industry:
        if REG_PRODUCT not in labels:
            labels.append(REG_PRODUCT)

    if "food" in industry or "agriculture" in industry:
        if REG_PRODUCT not in labels:
            labels.append(REG_PRODUCT)

    # Construction / building materials -> EPD
    if "construction" in industry or "infrastructure" in industry or CONSTRUCTION_KW.search(text_cs):
        if REG_EPD not in labels:
            labels.append(REG_EPD)

    # Transportation/Mobility + battery -> Battery Regulation
    if ("transportation" in industry or "mobility" in industry) and BATTERY_KW.search(text_cs):
        if REG_BATTERY not in labels:
            labels.append(REG_BATTERY)
        if REG_PRODUCT not in labels:
            labels.append(REG_PRODUCT)
    elif BATTERY_KW.search(text_cs) and "battery" in text:
        # generic battery mention even outside transport
        if REG_BATTERY not in labels:
            labels.append(REG_BATTERY)

    # DPP — only furniture/mattress/toy/textile/clothing
    if DPP_KW.search(text_cs):
        if REG_DPP not in labels:
            labels.append(REG_DPP)

    # Literal CSRD / EU listed
    if "csrd" in text or "eu listed" in text or "oslo børs" in text or "ftse" in text:
        if REG_CSRD not in labels:
            labels.append(REG_CSRD)

    # UK climate disclosure for UK-domain investors
    if domain.endswith(".uk") and ("investor" in persona.lower() or "fund" in persona.lower()):
        if REG_UK not in labels:
            labels.append(REG_UK)

    return labels


# ── DRIVER ───────────────────────────────────────────────────────────────────

def parse_score(row):
    try:
        return int(row.get("Lead Score (1-10)", "0") or 0)
    except (ValueError, TypeError):
        return 0


def is_stale(val):
    return (val or "").strip().lower() in STALE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--min-score", type=int, default=7)
    args = ap.parse_args()

    with open(args.input, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    for c in P3_FIELDS:
        if c not in fieldnames:
            fieldnames.append(c)

    targets = [i for i, r in enumerate(rows) if parse_score(r) >= args.min_score]
    print(f"total={len(rows)} score>={args.min_score}: {len(targets)}", flush=True)

    country_filled = 0
    country_src = Counter()
    reg_filled = 0
    reg_label_counts = Counter()
    urgency_bumped = 0

    for idx in targets:
        row = rows[idx]

        # ----- Country -----
        if is_stale(row.get("Country / HQ", "")):
            country, src = detect_country(row)
            if country:
                row["Country / HQ"] = country
                country_filled += 1
                country_src[src] += 1

        # ----- Regulatory -----
        existing_reg = (row.get("Regulatory Exposure", "") or "").strip()
        if is_stale(existing_reg):
            labels = derive_regulatory(row)
            if labels:
                joined = " + ".join(labels)
                row["Regulatory Exposure"] = joined
                reg_filled += 1
                for L in labels:
                    reg_label_counts[L] += 1
                # Bump urgency if currently Low/Minimal/stale
                cur_urg = (row.get("Estimated Urgency", "") or "").strip().lower()
                if cur_urg in {"", "low", "minimal", "unknown"}:
                    row["Estimated Urgency"] = "Medium"
                    urgency_bumped += 1

    with open(args.output, "w", encoding="utf-8-sig", newline="") as out:
        w = csv.DictWriter(out, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print()
    print(f"Country filled: {country_filled} / {len(targets)} hot rows")
    print(f"  source breakdown:")
    for k, v in country_src.most_common():
        print(f"    {k}: {v}")
    print()
    print(f"Regulatory filled: {reg_filled} / {len(targets)} hot rows")
    print(f"  label counts (multi-label, may sum > rows):")
    for k, v in reg_label_counts.most_common():
        print(f"    {k}: {v}")
    print()
    print(f"Estimated Urgency bumped to Medium: {urgency_bumped}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
