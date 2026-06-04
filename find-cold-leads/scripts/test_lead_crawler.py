"""Deterministic, offline, credit-free tests for the find-cold-leads plumbing.

These cover the helpers that protect against the original failures (vendor
domains kept as leads, blog titles stored as company names, third-party-snippet
contacts) plus the handoff-schema contract. No network, no Apollo credits.

Run:  python -m pytest scripts/test_lead_crawler.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import lead_crawler as lc


# --------------------------------------------------------------------------- #
# registrable_domain (eTLD+1)
# --------------------------------------------------------------------------- #

def test_registrable_domain_basic():
    assert lc.registrable_domain("https://www.sun-garden.de/about") == "sun-garden.de"
    assert lc.registrable_domain("http://shop.example.com") == "example.com"
    assert lc.registrable_domain("EXAMPLE.COM") == "example.com"


def test_registrable_domain_multi_label_suffix():
    assert lc.registrable_domain("https://www.acme.co.uk/x") == "acme.co.uk"
    assert lc.registrable_domain("https://team.acme.co.uk") == "acme.co.uk"
    assert lc.registrable_domain("https://acme.com.au") == "acme.com.au"


# --------------------------------------------------------------------------- #
# Blocklist — by registrable host only. The key near-misses.
# --------------------------------------------------------------------------- #

def test_blocklist_blocks_vendor_and_subdomains_and_case():
    assert lc.is_blocked("https://www.zoominfo.com/c/x") is True
    assert lc.is_blocked("https://de.zoominfo.com/c/x") is True          # subdomain
    assert lc.is_blocked("https://ZoomInfo.COM/c/x") is True             # case
    assert lc.is_blocked("https://apollo.io/companies") is True


def test_blocklist_allows_legit_company_with_token_in_name_or_path():
    # "Apollo Tyres" is a real ICP-class manufacturer; its DOMAIN is not a vendor.
    assert lc.is_blocked("https://www.apollotyres.com/en-in/") is False
    # A blocklist token appearing only in a URL PATH must not trigger a block.
    assert lc.is_blocked("https://acme-furniture.de/partners/zoominfo") is False


def test_dedupe_by_domain_keeps_first_per_registrable_domain():
    rows = [
        {"domain": "https://www.acme.de/a"},
        {"domain": "https://shop.acme.de/b"},   # same registrable domain
        {"domain": "https://other.com"},
    ]
    out = lc.dedupe_by_domain(rows)
    assert [lc.registrable_domain(r["domain"]) for r in out] == ["acme.de", "other.com"]


# --------------------------------------------------------------------------- #
# Company-name sanity — the #1 regression (blog title stored as company).
# --------------------------------------------------------------------------- #

def test_bad_company_names_rejected():
    bad = [
        "The production of textile fabrics in Germany: tradition, innovation an…Storchenwiege GmbH & Co. KG",
        "Top 100 Textile Manufacturing Companies in Germany (2026)",
        "Textile manufacturing Companies in Germany",
        "Setex: Home",
        "Best 50 Furniture Brands - 2025 Guide",
        "",
    ]
    for name in bad:
        assert lc.looks_like_bad_company_name(name) is True, name


def test_good_company_names_accepted():
    good = [
        "Storchenwiege GmbH & Co. KG",
        "BRANDS Fashion GmbH",
        "LOBERON GmbH",
        "Sun Garden",
        "Apollo Tyres",
        "Vaude Sport",
    ]
    for name in good:
        assert lc.looks_like_bad_company_name(name) is False, name


# --------------------------------------------------------------------------- #
# Region tagging from the SEARCH location filter (not enrich `country`).
# --------------------------------------------------------------------------- #

def test_region_for_location():
    assert lc.region_for_location("Germany") == {"region": "EU", "country": "Germany", "strict": True}
    assert lc.region_for_location("France")["strict"] is False
    assert lc.region_for_location("United States")["region"] == "US"
    assert lc.region_for_location("United Kingdom")["region"] == "UK"
    assert lc.region_for_location("European Union")["region"] == "EU"
    assert lc.region_for_location("Narnia")["region"] == "unknown"
    # ISO codes, endonyms, and "City Country" must resolve — else a Germany pull
    # typed as "DE"/"Deutschland" silently loses its UWG-strict posture.
    assert lc.region_for_location("DE") == {"region": "EU", "country": "Germany", "strict": True}
    assert lc.region_for_location("Deutschland")["strict"] is True
    assert lc.region_for_location("Munich Germany")["country"] == "Germany"
    assert lc.region_for_location("GB")["region"] == "UK"
    assert lc.region_for_location("us")["region"] == "US"


def test_compliance_posture_by_region():
    eu = lc.compliance_fields(lc.region_for_location("Germany"))
    assert eu["outreach_allowed_review"] == "needs review"
    assert eu["consent_status"] == "unknown"
    assert "UWG" in eu["legitimate_interest_basis"]            # DE strict flagged

    us = lc.compliance_fields(lc.region_for_location("United States"))
    assert "opt-out" in us["consent_status"]
    assert "CAN-SPAM" in us["outreach_allowed_review"]

    unknown = lc.compliance_fields(lc.region_for_location("Narnia"))
    assert unknown["outreach_allowed_review"] == "needs review"  # conservative EU default


# --------------------------------------------------------------------------- #
# Apollo -> handoff mapping + two-domains rule.
# --------------------------------------------------------------------------- #

ENRICHED = {
    "id": "66f67f94fd124c00010dc231",
    "first_name": "Lisa", "last_name": "Heyde", "name": "Lisa Heyde",
    "title": "Head of Sustainability", "headline": "Head of Sustainability at Sun Garden",
    "seniority": "head", "email": "l.heyde@sun-garden.de", "email_status": "verified",
    "linkedin_url": "http://www.linkedin.com/in/lisa-heyde",
    "city": "Neuenkirchen", "state": "North Rhine-Westphalia", "country": "Germany",
    "organization": {
        "name": "Sun Garden", "website_url": "http://www.sun-garden.eu",
        "primary_domain": "sun-garden.eu", "industry": "furniture",
        "estimated_num_employees": 4600, "short_description": "Garden furniture maker.",
    },
}


def test_map_apollo_person_uses_email_domain_for_outreach():
    row = lc.map_apollo_person(ENRICHED, lc.region_for_location("Germany"))
    # Outreach Domain comes from the EMAIL (.de), not organization.primary_domain (.eu).
    assert row["Domain"] == "sun-garden.de"
    assert row["Website"] == "http://www.sun-garden.eu"        # company identity
    assert row["Email"] == "l.heyde@sun-garden.de"
    assert row["email_verification_status"] == "verified"
    assert row["Company"] == "Sun Garden"
    assert row["LinkedIn"].endswith("/lisa-heyde")
    assert row["apollo_person_id"] == ENRICHED["id"]
    assert row["apollo_credits_consumed"] == 1
    assert row["contact_source"] == "apollo_enriched"
    assert row["outreach_allowed_review"] == "needs review"    # EU/DE wall stands


def test_map_apollo_person_no_email_is_apollo_free_zero_credit():
    # An id-bearing but email-less record reports 0 on the per-row credit field.
    # This is how BOTH a never-paid free-search row and a budget-exhausted
    # "apollo_free" fallback row arrive here, so counting it would phantom-charge
    # the run total. (The model's separate live counter gates overspend on match.)
    no_email = {**ENRICHED, "email": "", "email_status": ""}
    row = lc.map_apollo_person(no_email, lc.region_for_location("Germany"))
    assert row["contact_source"] == "apollo_free"
    assert row["apollo_credits_consumed"] == 0
    assert row["email_verification_status"] == "none"
    assert row["Domain"] == "sun-garden.eu"                    # falls back to primary_domain


def test_map_apollo_person_true_no_match_is_zero_credit():
    # A genuine no-match returns neither id nor email -> nothing was looked up.
    no_match = {**ENRICHED, "id": "", "email": "", "email_status": ""}
    row = lc.map_apollo_person(no_match, lc.region_for_location("Germany"))
    assert row["contact_source"] == "apollo_free"
    assert row["apollo_credits_consumed"] == 0
    assert row["apollo_person_id"] == ""


# --------------------------------------------------------------------------- #
# Contact provenance — evidence must be the company's own domain, never a vendor.
# --------------------------------------------------------------------------- #

def test_contact_provenance():
    # Good: the contact page is on the company's own registrable domain.
    assert lc.contact_provenance_ok("https://www.sun-garden.de/team", "sun-garden.de") is True
    # Bad: the "evidence" is a ZoomInfo snippet (the original #3 failure).
    assert lc.contact_provenance_ok("https://www.zoominfo.com/p/Mark-Mulingbayan", "fdc.com") is False
    # Bad: evidence domain does not match the company.
    assert lc.contact_provenance_ok("https://random-blog.com/x", "sun-garden.de") is False


# --------------------------------------------------------------------------- #
# Handoff conformance — bind to the REAL classifier ensure_headers, not a copy.
# --------------------------------------------------------------------------- #

def _load_classifier_required_columns() -> list[str] | None:
    """Parse the classifier's ensure_headers() required list from its source, so
    this test fails loudly if the classifier contract drifts."""
    path = Path.home() / ".claude/skills/climatepoint-contact-intelligence/references/climatepoint_classifier.py"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    start = text.find("required = [")
    if start == -1:
        return None
    end = text.find("]", start)
    block = text[start + len("required = ["):end]
    cols = [c.strip().strip('"').strip("'") for c in block.split(",")]
    return [c for c in cols if c]


# Pinned snapshot of the classifier's required columns (climatepoint-contact-
# intelligence ensure_headers, 2026-06). Used only when the live classifier source
# isn't on disk — e.g. this skill cloned standalone, without the classifier — so
# the conformance check still runs instead of silently skipping. When the
# classifier IS present, its source overrides this and also catches contract drift.
_CLASSIFIER_COLUMNS_SNAPSHOT = [
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


def test_classifier_columns_match_real_contract():
    real = _load_classifier_required_columns() or _CLASSIFIER_COLUMNS_SNAPSHOT
    missing = [c for c in real if c not in lc.LEADS_COLUMNS]
    assert not missing, f"handoff CSV is missing classifier columns: {missing}"


def test_leads_columns_have_no_duplicates():
    assert len(lc.LEADS_COLUMNS) == len(set(lc.LEADS_COLUMNS))


# --------------------------------------------------------------------------- #
# Query templates lead with INTENT, not generic keywords.
# --------------------------------------------------------------------------- #

def test_expand_queries_are_intent_first():
    _, theme = lc.load_theme("dpp-rollout-sectors", None)
    queries = lc.expand_queries(theme, "Germany", max_queries=6)
    assert queries, "expected queries"
    intent_tokens = ("ISO 14067", "PEFCR", "product carbon footprint", "EPD",
                     "environmental product declaration", "Digital Product Passport",
                     "life cycle assessment", "sustainability report")
    for q in queries:
        assert any(tok in q for tok in intent_tokens), q
        assert "Germany" in q


# --------------------------------------------------------------------------- #
# Offline discovery via fixture — blocklist + dedup + no network.
# --------------------------------------------------------------------------- #

def test_discover_candidates_offline_fixture(tmp_path):
    fixture = tmp_path / "fix.json"
    fixture.write_text(json.dumps({"organic": [
        {"title": "Sun Garden", "url": "https://www.sun-garden.de", "snippet": "garden furniture"},
        {"title": "Top 100 Textile Companies", "url": "https://ensun.io/list", "snippet": "directory"},
        {"title": "Vaude", "url": "https://www.vaude.com", "snippet": "outdoor gear EPD"},
        {"title": "Dup", "url": "https://shop.sun-garden.de/x", "snippet": "dup domain"},
    ]}), encoding="utf-8")
    candidates, sources, rejected = lc.discover_candidates(
        ["q"], provider="fixture", api_key=None, max_results=10, fixture=str(fixture),
    )
    domains = {c["domain"] for c in candidates}
    assert domains == {"sun-garden.de", "vaude.com"}            # ensun blocked, dup removed
    assert any(r["domain"] == "ensun.io" for r in rejected)
    assert sources and sources[0]["result_count"] == 4


def test_write_leads_roundtrip(tmp_path):
    row = lc.map_apollo_person(ENRICHED, lc.region_for_location("Germany"))
    out = tmp_path / "leads.xlsx"
    paths = lc.write_leads([row], str(out), run_config={"region": "EU"})
    csv_path = Path(paths["csv"])
    assert csv_path.exists()
    header = csv_path.read_text(encoding="utf-8-sig").splitlines()[0]
    for col in ("Domain", "Title", "Company", "Summary", "Headline", "Email"):
        assert col in header
