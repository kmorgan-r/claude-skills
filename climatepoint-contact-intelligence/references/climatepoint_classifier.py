#!/usr/bin/env python3
"""
ClimatePoint Contact Intelligence System — Batch Classifier

Reads enriched CSV (or raw CSV with basic fields), classifies contacts via
rule-based engine + Ollama LLM fallback, and writes UTF-8-BOM output.

Usage (local Ollama):
    python climatepoint_classifier.py \
        --input "Full Contact List - ClimatePoint (1) - Enriched.csv" \
        --output "Full Contact List - ClimatePoint (1) - Classified.csv" \
        --ollama-host http://localhost:11434 \
        --ollama-model llama3.2 \
        --batch-size 50

Usage (Ollama Cloud):
    python climatepoint_classifier.py \
        --input "Full Contact List - ClimatePoint (1) - Enriched.csv" \
        --output "Full Contact List - ClimatePoint (1) - Classified.csv" \
        --ollama-host https://ollama.com \
        --ollama-model kimi-k2.6:cloud \
        --ollama-api-key $OLLAMA_API_KEY \
        --batch-size 50

Requires: pip install ollama

Resume support: re-runs only process rows where Persona is empty / not set.
"""

import argparse
import csv
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

# ──────────────────────────────────────────────────────────────────────────────
# 1. CONFIGURATION — Rubrics, Keywords, Templates
# ──────────────────────────────────────────────────────────────────────────────

PERSONA_KEYWORDS: Dict[str, List[str]] = {
    "Sustainability Buyer": [
        "sustainability", "esg", "climate", "environment", "csr",
        "net zero", "carbon", "bærekraft", "miljø", "grønn"
    ],
    "Product / R&D Buyer": [
        "product", "r&d", "innovation", "packaging", "design",
        "engineering", "utvikling", "produkt", "cto", "chief technology",
        "scientific advisor", "research", "development"
    ],
    "Operations / Supply Chain Buyer": [
        "procurement", "supply chain", "operations", "logistics",
        "purchasing", "sourcing", "innkjøp", "drift", "coo",
        "chief operating"
    ],
    "Founder / Executive Sponsor": [
        "founder", "ceo", "managing director", "owner",
        "general manager", "daglig leder", "grundare", "adm dir",
        "chief executive"
    ],
    "Investor / Fund Persona": [
        "investment", "portfolio", "vc", "venture", "fund",
        "analyst", "partner", "investor", "private equity",
        "pe ", "capital", "finansiering", "principal",
        "managing partner", "founding partner", "investment director",
        "investment manager", "senior associate",
    ],
    "Marketing / Commercial": [
        "cmo", "marketing", "brand", "sales", "commercial",
        "communications", "salgs", "kommunikasjon"
    ],
    "Technical / Analyst": [
        "lca", "analyst", "consultant", "scientist", "researcher",
        "data", "rådgiver", "forsker", "risk", "head of risk",
        "technical", "professor"
    ],
    "Partner / Channel": [
        "consultant", "advisor", "agency", "accelerator",
        "incubator", "certification", "rådgivning"
    ],
}

LOW_FIT_KEYWORDS = [
    "hr", "legal", "admin", "student", "intern", "assistant",
    "receptionist", "bookkeeper", "accountant"
]

SENIORITY_KEYWORDS: Dict[str, List[str]] = {
    "C-level": ["ceo", "cfo", "coo", "cto", "cmo", "cso", "chief ", "founder", "owner", "president"],
    "VP / Director": ["vp", "vice president", "director", "head of", "leder", "sjef", "norgessjef"],
    "Manager": ["manager", "leder", "sjef"],
    "Analyst": ["analyst", "assistant", "koordinator", "rådgiver"],
}

PERSONA_BASE_SCORE: Dict[str, int] = {
    "Sustainability Buyer": 7,
    "Investor / Fund Persona": 6,
    "Founder / Executive Sponsor": 5,
    "Operations / Supply Chain Buyer": 6,
    "Product / R&D Buyer": 5,
    "Technical / Analyst": 4,
    "Marketing / Commercial": 3,
    "Partner / Channel": 3,
    "Low-fit / Other": 1,
    "Unknown": 2,
}

INDUSTRY_FIT_POSITIVE = [
    "cleantech", "manufacturing", "consumer goods", "industri",
    "food", "beverage", "energy", "mobility", "construction",
    "marine", "shipping", "agriculture", "forestry"
]
INDUSTRY_FIT_NEGATIVE = ["oil & gas", "fossil", "coal", "tobacco"]

NEED_TRIGGERS: Dict[str, Dict] = {
    "Product Carbon Footprint / LCA": {
        "signals": ["product", "manufacturing", "materials", "packaging", "claims"],
        "personas": ["Product / R&D Buyer", "Operations / Supply Chain Buyer", "Sustainability Buyer"],
    },
    "PEF / EU Product Environmental Footprint": {
        "signals": ["eu", "comparison", "claims", "regulation", "pef"],
        "personas": ["Product / R&D Buyer", "Marketing / Commercial", "Sustainability Buyer"],
    },
    "EPD / Construction Materials": {
        "signals": ["building", "furniture", "materials", "construction", "marine"],
        "personas": ["Operations / Supply Chain Buyer", "Technical / Analyst"],
    },
    "Investor Portfolio Impact": {
        "signals": ["vc", "fund", "accelerator", "sfdr", "impact", "portfolio"],
        "personas": ["Investor / Fund Persona"],
    },
    "Scope 3 / Supplier Footprint": {
        "signals": ["procurement", "supply chain", "supplier", "scope 3", "purchasing"],
        "personas": ["Operations / Supply Chain Buyer", "Sustainability Buyer"],
    },
    "Eco-design / Product Comparison": {
        "signals": ["r&d", "innovation", "alternative materials", "design", "comparison"],
        "personas": ["Product / R&D Buyer"],
    },
    "Sustainability Claims Validation": {
        "signals": ["carbon neutral", "low impact", "sustainable", "claims", "marketing"],
        "personas": ["Marketing / Commercial", "Sustainability Buyer"],
    },
    "Data Quality / Audit Support": {
        "signals": ["lca", "consultant", "audit", "methodology", "data"],
        "personas": ["Technical / Analyst"],
    },
}

OPPORTUNITY_MAP: Dict[Tuple[str, str], str] = {
    ("Sustainability Buyer", "Scope 3 / Supplier Footprint"): "Supplier footprint LCA + absolute target setting",
    ("Sustainability Buyer", "Product Carbon Footprint / LCA"): "Product carbon footprint + reporting",
    ("Investor / Fund Persona", "Investor Portfolio Impact"): "Portfolio-level climate screening + SFDR alignment",
    ("Product / R&D Buyer", "Eco-design / Product Comparison"): "Component LCA + eco-design benchmarking",
    ("Product / R&D Buyer", "Product Carbon Footprint / LCA"): "Product carbon footprint + reporting",
    ("Operations / Supply Chain Buyer", "Scope 3 / Supplier Footprint"): "Supply chain carbon assessment + procurement criteria",
    ("Marketing / Commercial", "Sustainability Claims Validation"): "Claims audit + PEF / EPD support",
    ("Technical / Analyst", "Data Quality / Audit Support"): "Methodology review + dataset validation",
    ("Founder / Executive Sponsor", "Not enough information"): "Strategic advisory + board-level sustainability roadmap",
}

OUTREACH_TEMPLATES: Dict[str, str] = {
    "Sustainability Buyer": (
        "{company} has made strong progress on {claims}. "
        "I'm seeing gaps in {gaps} that ClimatePoint can close with {offer}. Worth a brief call?"
    ),
    "Investor / Fund Persona": (
        "Your portfolio companies are facing {pressure}. ClimatePoint helps funds screen and report "
        "portfolio-level climate impact — including SFDR alignment. Could we share our approach with {company}?"
    ),
    "Operations / Supply Chain Buyer": (
        "{company}'s supply chain is a major emissions driver. We help companies build supplier-level "
        "carbon data and integrate it into procurement decisions. Relevant for {company}'s targets?"
    ),
    "Founder / Executive Sponsor": (
        "{company} is growing fast — and sustainability credibility is becoming a customer / investor / "
        "regulatory requirement. We help {industry} companies build defensible carbon and LCA foundations early. "
        "Open to a 15-minute conversation?"
    ),
    "Product / R&D Buyer": (
        "We're seeing {industry} companies use product-level LCA to win tenders / B2B contracts / consumer trust. "
        "ClimatePoint can benchmark {product} against competitors and identify material switches that cut footprint. Interested?"
    ),
    "Marketing / Commercial": (
        "{company}'s marketing claims around sustainability are under increasing scrutiny. "
        "We help validate and substantiate carbon claims with third-party LCA methodology. Relevant?"
    ),
    "Technical / Analyst": (
        "ClimatePoint offers independent dataset validation and methodology review for LCA teams. "
        "Could we support {company}'s technical work on {need}?"
    ),
    "Partner / Channel": (
        "{company} advises clients on sustainability strategy. ClimatePoint provides the technical LCA backbone. "
        "Worth exploring how we could partner?"
    ),
}

NEXT_ACTION_RULES: Dict[Tuple[int, str], str] = {
    # (min_score, max_score, persona) -> action
    (9, 10, "Any"): "Direct LinkedIn message + email + calendar link",
    (7, 8, "Sustainability Buyer"): "LinkedIn connection request + personalized message",
    (7, 8, "Operations / Supply Chain Buyer"): "LinkedIn connection request + personalized message",
    (7, 8, "Investor / Fund Persona"): "LinkedIn connection request + personalized message",
    (7, 8, "Founder / Executive Sponsor"): "Warm intro via mutual connection or board member",
    (7, 8, "Product / R&D Buyer"): "Warm intro via mutual connection or board member",
    (5, 6, "Any"): "Add to nurture sequence (relevant content + case study)",
    (3, 4, "Partner / Channel"): "Soft partnership inquiry",
    (3, 4, "Other"): "Deprioritize — quarterly check",
    (1, 2, "Any"): "Remove from active pipeline",
}

COMPETITOR_DOMAINS = {"3degreesinc.com", "3degrees.com", "ecoact.com", "carbontrust.com"}
INTERNAL_DOMAINS = {"climatepoint.com", "climatepoint.no"}

# ──────────────────────────────────────────────────────────────────────────────
# 2. OLLAMA CLIENT (supports local + cloud via ollama Python package)
# ──────────────────────────────────────────────────────────────────────────────

try:
    import ollama as ollama_lib
    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False
    ollama_lib = None  # type: ignore


class OllamaClient:
    def __init__(self, host: str, model: str, api_key: Optional[str] = None):
        self.host = host.rstrip("/")
        self.model = model
        self.api_key = api_key
        self._client = None
        if HAS_OLLAMA:
            kwargs = {"host": self.host}
            if api_key:
                kwargs["headers"] = {"Authorization": f"Bearer {api_key}"}
            self._client = ollama_lib.Client(**kwargs)

    def _chat(self, prompt: str, temperature: float = 0.2, num_predict: int = 60) -> str:
        if not HAS_OLLAMA:
            raise RuntimeError("ollama Python package not installed. Run: pip install ollama")
        if self._client is None:
            raise RuntimeError("Ollama client not initialized")

        messages = [{"role": "user", "content": prompt}]
        resp = self._client.chat(
            model=self.model,
            messages=messages,
            options={"temperature": temperature, "num_predict": num_predict},
        )
        return resp.message.content.strip()

    def classify_persona(self, title: str, company: str, summary: str, headline: str) -> str:
        prompt = f"""Given this contact:
- Title: {title}
- Company: {company}
- Industry context: {headline}
- Summary: {summary}

Classify into one persona:
1. Sustainability Buyer
2. Product / R&D Buyer
3. Operations / Supply Chain Buyer
4. Founder / Executive Sponsor
5. Investor / Fund Persona
6. Marketing / Commercial
7. Technical / Analyst
8. Partner / Channel
9. Low-fit / Other

Explain reasoning in one sentence. Return only the persona name."""
        return self._chat(prompt, temperature=0.2, num_predict=60)

    def generate_outreach_angle(self, persona: str, company: str, need: str, title: str) -> str:
        prompt = f"""Write a 1-2 sentence personalized outreach message for:
- Persona: {persona}
- Company: {company}
- Their likely need: {need}
- Their title: {title}

Keep it natural, specific, and under 40 words. No generic fluff."""
        return self._chat(prompt, temperature=0.7, num_predict=120).replace("\n", " ")


# ──────────────────────────────────────────────────────────────────────────────
# 3. CLASSIFICATION ENGINE
# ──────────────────────────────────────────────────────────────────────────────

def extract_domain(email: str) -> str:
    if not email or "@" not in email:
        return ""
    return email.split("@")[-1].strip().lower()


def detect_seniority(title: str) -> str:
    t = title.lower()
    for level, keywords in SENIORITY_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return level
    return "Manager"


FUND_DOMAIN_HINTS = {"vc", "fund", "capital", "partners", "ventures", "equity", "pe ", "investment"}


def normalize_text_for_keywords(text: str) -> str:
    """Strip 'former' / 'ex-' / 'previous' prefixes from titles so current role drives classification."""
    t = text.lower()
    t = re.sub(r'\bformer\b', '', t)
    t = re.sub(r'\bex[-\s]?\b', '', t)
    t = re.sub(r'\bpreviously\b.*?(?=\b(at|and|/|–|—|,)\b|$)', '', t)
    return t


def rule_based_persona(title: str, headline: str, summary: str, domain: str = "") -> str:
    text = normalize_text_for_keywords(f"{title} {headline} {summary}")

    t = title.lower()
    if any(re.search(rf'\b{re.escape(kw)}\b', t) for kw in LOW_FIT_KEYWORDS):
        return "Low-fit / Other"

    scores: Dict[str, int] = defaultdict(int)
    for persona, keywords in PERSONA_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                scores[persona] += 1

    # Boost Investor for fund domains
    if domain:
        dom = domain.lower()
        if any(h in dom for h in FUND_DOMAIN_HINTS):
            scores["Investor / Fund Persona"] += 1

    if not scores:
        return ""

    # Tie-break: prefer higher-base-score persona when scores equal
    base_order = {
        "Sustainability Buyer": 7,
        "Investor / Fund Persona": 6,
        "Operations / Supply Chain Buyer": 6,
        "Founder / Executive Sponsor": 5,
        "Product / R&D Buyer": 5,
        "Technical / Analyst": 4,
        "Marketing / Commercial": 3,
        "Partner / Channel": 3,
        "Low-fit / Other": 1,
    }

    max_score = max(scores.values())
    tied = [p for p, s in scores.items() if s == max_score]
    best = max(tied, key=lambda p: base_order.get(p, 0))

    if scores[best] > 0:
        return best
    return ""


CANONICAL_PERSONAS = [
    "Sustainability Buyer",
    "Product / R&D Buyer",
    "Operations / Supply Chain Buyer",
    "Founder / Executive Sponsor",
    "Investor / Fund Persona",
    "Marketing / Commercial",
    "Technical / Analyst",
    "Partner / Channel",
    "Low-fit / Other",
]


def normalize_persona(raw: str) -> str:
    """Map a free-form LLM persona response onto a canonical persona name.

    Ollama is told to "return only the persona name" but frequently returns a
    verbose sentence ("Based on the title ..., I would classify this as
    Product / R&D Buyer"). Storing that raw string corrupts downstream lookups
    (PERSONA_BASE_SCORE, classify_need, determine_next_action) silently and
    permanently (--resume never revisits a non-empty Persona).
    """
    if not raw:
        return "Unknown"
    txt = raw.strip()
    # Strip leading numbers / bullets
    txt = re.sub(r"^\s*\d+[\.\):]\s*", "", txt)
    # Take first line if multi-line
    txt = txt.splitlines()[0] if txt else ""
    low = txt.lower()
    for p in CANONICAL_PERSONAS:
        if p.lower() in low:
            return p
    aliases = {
        "sustainability": "Sustainability Buyer",
        "esg": "Sustainability Buyer",
        "product": "Product / R&D Buyer",
        "r&d": "Product / R&D Buyer",
        "research": "Product / R&D Buyer",
        "supply chain": "Operations / Supply Chain Buyer",
        "operations": "Operations / Supply Chain Buyer",
        "procurement": "Operations / Supply Chain Buyer",
        "founder": "Founder / Executive Sponsor",
        "ceo": "Founder / Executive Sponsor",
        "executive": "Founder / Executive Sponsor",
        "investor": "Investor / Fund Persona",
        "fund": "Investor / Fund Persona",
        "venture": "Investor / Fund Persona",
        "vc": "Investor / Fund Persona",
        "marketing": "Marketing / Commercial",
        "commercial": "Marketing / Commercial",
        "sales": "Marketing / Commercial",
        "analyst": "Technical / Analyst",
        "technical": "Technical / Analyst",
        "partner": "Partner / Channel",
        "channel": "Partner / Channel",
        "consultant": "Partner / Channel",
        "low-fit": "Low-fit / Other",
        "low fit": "Low-fit / Other",
        "other": "Low-fit / Other",
    }
    for k, v in aliases.items():
        if k in low:
            return v
    return "Unknown"


# Aggregate Ollama-failure tracking. Without this, a mid-run Ollama outage
# (expired key, host down, rate limit) silently stamps every ambiguous row
# "Unknown" and the script still exits 0 — a real quality regression that's
# invisible unless someone scrolls the console. classify_persona increments
# on exception and resets on a successful LLM call; the driver aborts when
# CONSECUTIVE failures cross OLLAMA_FAILURE_ABORT so the run fails loudly
# instead of producing a batch of Unknown personas.
_ollama_stats = {"failures": 0, "consecutive": 0}
OLLAMA_FAILURE_ABORT = 5


def classify_persona(title: str, company: str, summary: str, headline: str,
                     ollama: Optional[OllamaClient], domain: str = "") -> str:
    persona = rule_based_persona(title, headline, summary, domain)
    if persona:
        return persona

    # Skip LLM for clearly empty / generic titles
    clean_title = title.strip().lower()
    if not clean_title or clean_title in ("not found", "unknown", "generic contact", "info", "hello"):
        return "Low-fit / Other"

    if ollama and title and company:
        try:
            raw = ollama.classify_persona(title, company, summary, headline)
            _ollama_stats["consecutive"] = 0
            return normalize_persona(raw)
        except Exception as e:
            print(f"  [Ollama fallback failed: {e}]")
            _ollama_stats["failures"] += 1
            _ollama_stats["consecutive"] += 1
    return "Unknown"


def classify_need(persona: str, title: str, company: str, summary: str) -> str:
    text = f"{title} {summary}".lower()
    scores: Dict[str, int] = defaultdict(int)

    for need, data in NEED_TRIGGERS.items():
        if persona not in data["personas"]:
            continue
        for signal in data["signals"]:
            if re.search(rf'\b{re.escape(signal)}\b', text):
                scores[need] += 1

    if scores:
        return max(scores, key=scores.get)

    # Defaults by persona
    defaults = {
        "Sustainability Buyer": "Scope 3 / Supplier Footprint",
        "Investor / Fund Persona": "Investor Portfolio Impact",
        "Product / R&D Buyer": "Product Carbon Footprint / LCA",
        "Operations / Supply Chain Buyer": "Scope 3 / Supplier Footprint",
        "Marketing / Commercial": "Sustainability Claims Validation",
        "Technical / Analyst": "Data Quality / Audit Support",
        "Founder / Executive Sponsor": "Not enough information",
        "Partner / Channel": "Not enough information",
    }
    return defaults.get(persona, "Not enough information")


def score_lead(persona: str, seniority: str, title: str, company: str,
               summary: str, domain: str) -> int:
    base = PERSONA_BASE_SCORE.get(persona, 2)
    score = base

    # Seniority
    if seniority == "C-level":
        score += 2
    elif seniority in ("VP / Director",):
        score += 1

    # Industry fit heuristic from summary/company
    text = f"{company} {summary}".lower()
    if any(ind in text for ind in INDUSTRY_FIT_POSITIVE):
        score += 1
    if any(ind in text for ind in INDUSTRY_FIT_NEGATIVE):
        score -= 1

    # Physical product / manufacturing signals
    if any(kw in text for kw in ["manufacturing", "product", "hardware", "device", "vehicle", "ship", "turbine"]):
        score += 1
    if any(kw in text for kw in ["supply chain", "procurement", "sourcing", "logistics"]):
        score += 1

    # Investor flag
    if persona == "Investor / Fund Persona":
        score += 1

    # Regulatory exposure
    if any(kw in text for kw in ["csrd", "sfdr", "scope 3", "sbti", "tcfd", "pef"]):
        score += 1

    # ESG content gap = opportunity
    if persona in ("Sustainability Buyer", "Operations / Supply Chain Buyer") and "no esg" in text:
        score += 1

    # Competitor / internal deprioritize
    if domain in COMPETITOR_DOMAINS:
        score = min(score, 3)
    # Internal-contact check: domain is precise; company name catches internal
    # employees on non-ClimatePoint mail. Do NOT scan `text` (which includes the
    # web-enriched summary) — external prospects' summaries often mention
    # ClimatePoint as a competitor/reference and would be falsely zeroed.
    if domain in INTERNAL_DOMAINS or "climatepoint" in company.lower():
        return 0

    return max(0, min(10, score))


def map_opportunity(persona: str, need: str) -> str:
    key = (persona, need)
    if key in OPPORTUNITY_MAP:
        return OPPORTUNITY_MAP[key]
    # Fallbacks
    if persona == "Sustainability Buyer":
        return "Carbon analysis + LCA"
    if persona == "Investor / Fund Persona":
        return "Portfolio-level climate screening + SFDR alignment"
    if persona == "Product / R&D Buyer":
        return "Component LCA + eco-design benchmarking"
    if persona == "Operations / Supply Chain Buyer":
        return "Supply chain carbon assessment + procurement criteria"
    if persona == "Marketing / Commercial":
        return "Claims audit + PEF / EPD support"
    if persona == "Technical / Analyst":
        return "Methodology review + dataset validation"
    if persona == "Founder / Executive Sponsor":
        return "Strategic advisory + board-level sustainability roadmap"
    return "Partner / Channel"


def build_outreach_angle(persona: str, company: str, need: str,
                         title: str, summary: str,
                         ollama: Optional[OllamaClient]) -> str:
    if persona == "Low-fit / Other":
        return "Not a priority — low fit for current ICP"
    if persona == "Unknown":
        return "Attempt to find LinkedIn/role or deprioritize"

    # Use template by default; Ollama only for high-value leads to save time
    score = PERSONA_BASE_SCORE.get(persona, 5)
    if ollama and score >= 6:
        try:
            return ollama.generate_outreach_angle(persona, company, need, title)
        except Exception:
            pass

    template = OUTREACH_TEMPLATES.get(persona, OUTREACH_TEMPLATES["Sustainability Buyer"])
    claims = "sustainability commitments" if "sustain" in summary.lower() else "climate ambitions"
    gaps = "supplier-level carbon data" if "scope 3" in need.lower() else "product-level footprint methodology"
    offer = "our LCA platform" if "lca" in need.lower() else "ClimatePoint's carbon analysis"
    pressure = "regulatory pressure and LP ESG demands"
    industry = "industrial"
    product = "their products"

    return template.format(
        company=company or "your company",
        claims=claims,
        gaps=gaps,
        offer=offer,
        pressure=pressure,
        industry=industry,
        product=product,
        need=need,
    )


def determine_next_action(score: int, persona: str) -> str:
    if score >= 9:
        return NEXT_ACTION_RULES.get((9, 10, "Any"), "Direct outreach")
    if score >= 7:
        for (lo, hi, p), action in NEXT_ACTION_RULES.items():
            if lo <= score <= hi and (p == persona or p == "Any"):
                return action
        return "LinkedIn connection request + personalized message"
    if score >= 5:
        return "Add to nurture sequence (relevant content + case study)"
    if score >= 3:
        if persona == "Partner / Channel":
            return "Soft partnership inquiry"
        return "Deprioritize — quarterly check"
    if score == 0:
        return "Exclude from outreach — internal contact"
    return "Remove from active pipeline"


def infer_account_fields(row: Dict[str, str], persona: str, company: str, domain: str, summary: str) -> Dict[str, str]:
    """Basic heuristic account research — not deep, but better than blank.
    Preserves any existing non-empty values."""
    text = f"{company} {summary}".lower()
    industry = "Unknown"
    if any(kw in text for kw in ["vc", "fund", "investment", "capital", "private equity"]):
        industry = "Venture Capital / Investment"
    elif any(kw in text for kw in ["energy", "renewable", "solar", "wind", "power"]):
        industry = "Renewable Energy"
    elif any(kw in text for kw in ["transport", "mobility", "vehicle", "shipping", "maritime"]):
        industry = "Transportation / Mobility"
    elif any(kw in text for kw in ["food", "beverage", "agriculture", "farm"]):
        industry = "Food & Agriculture"
    elif any(kw in text for kw in ["tech", "software", "saas", "ai", "data"]):
        industry = "Technology / Software"
    elif any(kw in text for kw in ["manufacturing", "industrial", "production", "hardware"]):
        industry = "Manufacturing / Industrial"

    size = "Unknown"
    if any(kw in text for kw in ["large enterprise", "20000+", "public", "oslo børs", "ftse"]):
        size = "Large enterprise"
    elif any(kw in text for kw in ["sme", "startup", "scale-up", "50 employees", "20 employees"]):
        size = "SME"
    elif any(kw in text for kw in ["micro", "solo", "<10", "founder only"]):
        size = "Micro (<10 employees)"

    has_physical = "Yes" if any(kw in text for kw in ["product", "hardware", "device", "vehicle", "ship", "turbine", "manufacturing"]) else "No"
    has_mfg = "Yes" if any(kw in text for kw in ["manufacturing", "supply chain", "production", "logistics", "procurement"]) else "No"
    has_investors = "Yes" if any(kw in text for kw in ["vc-backed", "series", "funded", "investor", "portfolio"]) else "No"
    esg = "Extensive" if any(kw in text for kw in ["annual report", "sustainability report", "tcfd", "gri", "sbti"]) else "Minimal"
    reg = "CSRD" if any(kw in text for kw in ["csrd", "eu listed"]) else "Minimal"

    result = {
        "Company Name": company or domain.split(".")[0].capitalize(),
        "Website": f"https://www.{domain}" if domain else "",
        "Industry": industry,
        "Company Size": size,
        "Revenue / Funding Stage": "Unknown",
        "Country / HQ": "Unknown",
        "Product Type": "Unknown",
        "Sustainability Claims": "None found" if esg == "Minimal" else "Yes (from research)",
        "Regulatory Exposure": reg,
        "Has Physical Product": has_physical,
        "Has Manufacturing / Supply Chain": has_mfg,
        "Has Investors / Portfolio": has_investors,
        "Existing ESG Content": esg,
        "Likely LCA Need": "Medium" if persona in ("Sustainability Buyer", "Operations / Supply Chain Buyer", "Product / R&D Buyer") else "Low",
        "Estimated Urgency": "Medium" if reg != "Minimal" else "Low",
        "Recommended Offer": map_opportunity(persona, classify_need(persona, "", company, summary)),
    }
    # Preserve existing non-empty values
    for k, v in result.items():
        existing = row.get(k, "")
        if existing and str(existing).strip() and str(existing).strip().lower() not in ("unknown", "none found", "minimal", "", "not enough information"):
            result[k] = existing
    return result


# ──────────────────────────────────────────────────────────────────────────────
# 4. CSV I/O
# ──────────────────────────────────────────────────────────────────────────────

def read_csv(path: str) -> Tuple[List[str], List[Dict[str, str]]]:
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)
    return headers, rows


def ensure_headers(headers: List[str]) -> List[str]:
    """Add any missing classification columns."""
    required = [
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
    new_headers = list(headers)
    for h in required:
        if h not in new_headers:
            new_headers.append(h)
    return new_headers


def _csv_safe(value):
    """Neutralize CSV formula injection (OWASP). Several enriched fields
    (Company, Title, Summary, Headline) come from live web-search results —
    untrusted text. A leading = + - @ turns a cell into a live Excel
    formula/DDE payload when the output is opened in Excel (SKILL.md ships
    utf-8-sig for exactly that). Prefix such cells with a single quote so
    Excel treats them as literal text. None is preserved (csv writes "")."""
    if value is None:
        return value
    s = str(value)
    if s[:1] in ("=", "+", "-", "@"):
        return "'" + s
    return s


def atomic_write_csv(path: str, fieldnames: List[str], rows: List[Dict[str, str]],
                     extrasaction: str = "ignore") -> None:
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


def write_csv(path: str, headers: List[str], rows: List[Dict[str, str]]) -> None:
    """Backwards-compatible wrapper; writes atomically."""
    atomic_write_csv(path, headers, rows, extrasaction="raise")


# ──────────────────────────────────────────────────────────────────────────────
# 5. MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ClimatePoint Contact Classifier")
    parser.add_argument("--input", required=True, help="Input CSV path")
    parser.add_argument("--output", required=True, help="Output CSV path")
    parser.add_argument("--ollama-host", default="http://localhost:11434", help="Ollama host (local or cloud)")
    parser.add_argument("--ollama-model", default="kimi-k2.6:cloud", help="Ollama model name")
    parser.add_argument(
        "--ollama-api-key",
        default=os.environ.get("OLLAMA_API_KEY"),
        help="Optional Ollama API key (falls back to OLLAMA_API_KEY env var)"
    )
    parser.add_argument("--batch-size", type=int, default=50, help="Rows per batch")
    parser.add_argument("--resume", action="store_true", help="Skip rows already classified")
    parser.add_argument("--deep-research-min-score", type=int, default=7,
                        help="Minimum score to run account research heuristics")
    args = parser.parse_args()

    ollama = None
    try:
        ollama = OllamaClient(args.ollama_host, args.ollama_model, args.ollama_api_key)
        # Quick health check
        if HAS_OLLAMA and ollama._client:
            ollama._client.list()
        else:
            urllib.request.urlopen(f"{args.ollama_host}/api/tags", timeout=5)
        print(f"[OK] Ollama connected: {args.ollama_model} @ {args.ollama_host}")
    except Exception as e:
        print(f"[WARN] Ollama not available ({e}). Rule-based only, no LLM fallback.")
        ollama = None

    headers, rows = read_csv(args.input)
    headers = ensure_headers(headers)
    print(f"[INFO] Loaded {len(rows)} rows, {len(headers)} columns")

    processed = 0
    skipped = 0

    for i, row in enumerate(rows):
        # --resume skips completed work, not failed work. "Unknown" is the
        # stamp classify_persona leaves when the LLM fallback failed (or the
        # rule engine genuinely couldn't classify) — it is a placeholder, not
        # a classification. Treating it as populated (the old non-empty check)
        # permanently locked failed rows out of retry on every future --resume.
        if args.resume:
            existing_persona = (row.get("Persona") or "").strip()
            if existing_persona and existing_persona.lower() != "unknown":
                skipped += 1
                continue

        email = row.get("Email", "")
        domain = extract_domain(email)
        row["Domain"] = domain

        title = row.get("Title", "") or ""
        company = row.get("Company", "") or ""
        summary = row.get("Summary", "") or ""
        headline = row.get("Headline", "") or ""

        # Skip internal / competitor. Scan company + domain only, NOT the
        # web-enriched summary (external prospects' summaries frequently
        # mention ClimatePoint as a competitor/reference → false positive).
        if domain in INTERNAL_DOMAINS or "climatepoint" in company.lower():
            row["Persona"] = "Low-fit / Other"
            row["Lead Score (1-10)"] = "0"
            row["Need State"] = "Internal"
            row["Opportunity Type"] = "Internal"
            row["Outreach Angle"] = "ClimatePoint co-founder — internal contact"
            row["Next Action"] = "Exclude from outreach — internal contact"
            processed += 1
            continue

        if domain in COMPETITOR_DOMAINS:
            row["Persona"] = "Low-fit / Other"
            row["Lead Score (1-10)"] = "3"
            row["Need State"] = "Not enough information"
            row["Opportunity Type"] = "Low-fit / Other"
            row["Outreach Angle"] = "Competitor/partner — identify role before outreach"
            row["Next Action"] = "Attempt to find LinkedIn/role or deprioritize"
            processed += 1
            continue

        # Classification
        persona = classify_persona(title, company, summary, headline, ollama, domain)

        # Abort loudly on sustained Ollama failure instead of silently
        # degrading the rest of the batch to "Unknown" (exit 0). Saves the
        # checkpoint so --resume picks up here once Ollama is back.
        if _ollama_stats["consecutive"] >= OLLAMA_FAILURE_ABORT:
            print(
                f"[ABORT] {OLLAMA_FAILURE_ABORT} consecutive Ollama failures "
                f"(total {_ollama_stats['failures']}). Stopping to avoid a "
                f"silent Unknown batch — check --ollama-host / OLLAMA_API_KEY. "
                f"Checkpoint saved; rerun with --resume once Ollama is available.",
                file=sys.stderr,
            )
            write_csv(args.output, headers, rows)
            sys.exit(1)

        seniority = detect_seniority(title)
        need = classify_need(persona, title, company, summary)
        score = score_lead(persona, seniority, title, company, summary, domain)
        opportunity = map_opportunity(persona, need)
        outreach = build_outreach_angle(persona, company, need, title, summary, ollama)
        next_action = determine_next_action(score, persona)

        # Only overwrite classification fields if empty or if resume is off
        def set_if_empty(key: str, val: str):
            existing = str(row.get(key, "")).strip()
            if not existing or existing.lower() in ("unknown", "", "not enough information"):
                row[key] = val

        set_if_empty("Department / Function", persona.replace(" Buyer", "").replace(" Persona", "").replace(" Sponsor", ""))
        set_if_empty("Seniority", seniority)
        set_if_empty("Persona", persona)
        set_if_empty("Lead Score (1-10)", str(score))
        set_if_empty("Need State", need)
        set_if_empty("Opportunity Type", opportunity)
        set_if_empty("Outreach Angle", outreach)
        set_if_empty("Next Action", next_action)

        # Account research heuristic (lightweight)
        if score >= args.deep_research_min_score:
            account = infer_account_fields(row, persona, company, domain, summary)
            for k, v in account.items():
                if k in headers:
                    set_if_empty(k, v)

        processed += 1

        if processed % args.batch_size == 0:
            print(f"[PROGRESS] {processed} processed, {skipped} skipped (total {i+1}/{len(rows)})")
            # Checkpoint write
            write_csv(args.output, headers, rows)

    write_csv(args.output, headers, rows)
    print(f"[DONE] {processed} rows classified. Output: {args.output}")


if __name__ == "__main__":
    main()
