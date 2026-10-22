#!/usr/bin/env python3
"""Build the EPD expiry lead list from the NSF public listings service.

Source of truth: the JSON API behind https://listings.nsf.org. The old
info.nsf.org HTML page is unreachable from every network we tried, so the
API is both the only route and a better one. Two endpoints, both POST JSON:

  products/searchstandardproducts  -> every EPD listing, with validity period
  company/search/products          -> per-company address, phone, website

Each listing links to the EPD PDF in NSF's public blob storage. The PDF is
where the lead lives: it names the declaration holder, who prepared the
declaration, the verifier and the PCR reviewer, and carries the company's
own address block and website.

The rubric is applied per manufacturer, not per EPD. An issue rubric item
fires once for the company however many of its EPDs are expiring.

Usage:
    python build_lead_list.py --out reports/epd-expiry-leads.xlsx
    python build_lead_list.py --start 2027-11-01 --end 2028-10-31 --refresh
    python build_lead_list.py --no-pdfs          # listing data only, no PDF parse
"""
import argparse
import concurrent.futures
import csv
import json
import os
import re
import sys
import urllib.request
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)

SERVICE = "https://listings-service.nsf.org/publiclistingservice/"
PDF_BASE = "https://nsfpubliclistingsprodeu.blob.core.windows.net/prod/Sustain/ProdCert/"
UA = "Mozilla/5.0 (compatible; epd-expiry-leads/1.0)"

# ---------------------------------------------------------------- materials
# Grounded in the product categories NSF itself publishes (see
# references/buy-clean-materials.md for the evidence and the limits).
#
#   ca:      covered by the California Buy Clean Act
#   states:  other state Buy Clean programmes that cover the material
#
# Only "Thermal Insulation" maps to a California category. NSF's registry
# carries no flat glass, rebar or structural steel product category, so
# those leads have to come from the non-NSF registries, which are
# terms-gated (see references/sources.md).
MATERIALS = {
    "insulation": {"ca": True, "states": ["MN", "TX"]},
    "concrete": {"ca": False, "states": ["CO", "MA", "MD", "MN", "NJ", "NY", "OR", "WA", "WI", "IL"]},
    "precast": {"ca": False, "states": ["CO", "WA"]},
    "asphalt": {"ca": False, "states": ["CO", "MA", "MD", "NY"]},
    "steel": {"ca": False, "states": ["CO", "MA", "MD", "MN", "NJ", "NY", "OR", "WA", "WI", "IL"]},
    "gypsum": {"ca": False, "states": ["MN"]},
    "coatings": {"ca": False, "states": []},
    "roofing": {"ca": False, "states": []},
    "barrier": {"ca": False, "states": []},
    "flooring": {"ca": False, "states": []},
    "furniture": {"ca": False, "states": []},
    "panels": {"ca": False, "states": []},
    "masonry": {"ca": False, "states": []},
    "other": {"ca": False, "states": []},
}

# NSF product category -> material. Matched case-insensitively.
CATEGORY_MATERIAL = {
    "thermal insulation": "insulation",
    "spf insulation": "insulation",
    "continuous wall insulation": "insulation",
    "roof insulation": "insulation",
    "concrete": "concrete",
    "precast concrete": "precast",
    "articulating concrete block": "concrete",
    "interlocking concrete pavers": "concrete",
    "concrete coatings": "coatings",
    "asphalt shingles": "asphalt",
    "asphalt membranes": "asphalt",
    "steel brackets": "steel",
    "gypsum": "gypsum",
    "gypsum board products": "gypsum",
    "gypsum panel products": "gypsum",
    "gypsum roof board": "gypsum",
    "interior coatings": "coatings",
    "exterior coatings": "coatings",
    "interior/exterior coatings": "coatings",
    "exterior/interior coatings": "coatings",
    "powder coatings": "coatings",
    "roof coatings": "coatings",
    "single ply roofing": "roofing",
    "water-resistive & air barriers": "barrier",
    "resinous flooring": "flooring",
    "flooring": "flooring",
    "residential broadloom": "flooring",
    "seating": "furniture",
    "storage": "furniture",
    "tables": "furniture",
    "table": "furniture",
    "workspace": "furniture",
    "workspace furniture": "furniture",
    "upholstery": "furniture",
    "roof and wall panels": "panels",
    "bricks": "masonry",
}

# ------------------------------------------------------------- third parties
# Parties that show up in EPD PDFs and are never the lead: the programme
# operator, the verifier, the PCR reviewer and the LCA consultancies.
THIRD_PARTY_DOMAINS = {
    "nsf.org", "nsf.com", "astm.org", "epa.gov", "3e.com", "paintcare.org",
    "ecoform.com", "industrial-ecology.com", "athenasmi.org",
    "wapsustainability.com", "intertek.com", "sphera.com", "quantis.com",
    "truenorthcollective.net", "ramboll.com", "sustainablesolutionscorporation.com",
    "ul.com", "ulprospector.com", "buildingtransparency.org",
}

# Known EPD consultancies. The rubric penalises an EPD developed by one of
# these, because the consultant usually drives the renewal.
CONSULTANTS = {
    "ecoform.com", "industrial-ecology.com", "athenasmi.org",
    "wapsustainability.com", "sphera.com", "quantis.com", "ramboll.com",
    "truenorthcollective.net", "sustainablesolutionscorporation.com", "3e.com",
    "ericblond.com",
}

# The same bodies named in the PDF rather than in an email domain.
CONSULTANT_ORGS = (
    "ecoform", "industrial ecology", "athena sustainability", "wap sustainability",
    "sphera", "quantis", "ramboll", "true north", "sustainable solutions corporation",
    "eric blond", "3e verification", "intertek",
)

# Bodies that author other manufacturers' EPDs while supplying them. They
# are not the manufacturer and not the lead, so an EPD they wrote is not
# an in-house declaration, and the renewal will not run through the
# manufacturer. Master Builders Solutions writes the concrete EPDs for
# several registrants in this list.
THIRD_PARTY_AUTHORS = {
    "mbcc-group.com", "masterbuildersolutions.com", "sika.com",
    "wapconsulting.com", "oneclicklca.com",
}
THIRD_PARTY_AUTHOR_NAMES = ("master builders solutions", "wap sustainability",
                             "one click lca")

# Operator-run tools that generate an EPD without a human author. The rubric
# penalises these, but only when no human preparer is named.
AUTOMATED_SOFTWARE = ("builder", "generator", "autogen", "one click")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
BARE_URL_RE = re.compile(r"(?<![\w/@.])(www\.[A-Za-z0-9.-]{4,60}\.[A-Za-z]{2,10})")
ANY_URL_RE = re.compile(r"https?://[^\s)>\]]{6,90}")


# ------------------------------------------------------------------ helpers
def post(path, body, timeout=90):
    req = urllib.request.Request(
        SERVICE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def get_bytes(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def empty_search():
    return {"searchText": None, "category": None, "subCategory": None,
            "standPrgGrp": "EPD", "standPrgGrpName": None, "company": None,
            "companyCode": None, "location": None, "searchParameters": [],
            "exclusiveSearchParameters": [], "region": None, "country": None}


def domain_of(value):
    value = (value or "").strip().lower()
    value = re.sub(r"^https?://", "", value).split("/")[0]
    value = value.split("@")[-1]
    return value.removeprefix("www.")


def _matches(domain, names):
    return any(domain == n or domain.endswith("." + n) for n in names)


def is_third_party(email_or_domain):
    return _matches(domain_of(email_or_domain), THIRD_PARTY_DOMAINS)


def is_consultant(email_or_domain):
    return _matches(domain_of(email_or_domain), CONSULTANTS)


def is_third_party_author(email_or_domain):
    return _matches(domain_of(email_or_domain), THIRD_PARTY_AUTHORS)


def parse_period(text):
    """'06/24/2022 - 06/24/2027' -> ('2022-06-24', '2027-06-24').

    Month first, year last, in US order. Getting this backwards silently
    filters the wrong rows, so it is worth its own test.
    """
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})\s*-\s*(\d{1,2})/(\d{1,2})/(\d{4})", text or "")
    if not m:
        return None, None
    mo, da, yr, emo, eda, eyr = m.groups()
    return (f"{int(yr):04d}-{int(mo):02d}-{int(da):02d}",
            f"{int(eyr):04d}-{int(emo):02d}-{int(eda):02d}")


def is_us_address(address):
    """NSF company addresses end with the country for non-US registrants."""
    low = (address or "").lower()
    if "united states" in low:
        return True
    return not re.search(r"\b(egypt|saudi|canada|mexico|china|india|germany)\b", low)


# ------------------------------------------------------------------ listing
def fetch_listings():
    """Every NSF EPD listing, flattened to one row per EPD."""
    data = post("products/searchstandardproducts", empty_search())
    rows = []
    for group in data.get("data") or []:
        for result in group.get("resultSet") or []:
            for product in result.get("products") or []:
                issued, expires = parse_period(product.get("CERTIFICATION_PERIOD"))
                rows.append({
                    "epd_number": product.get("CERTIFICATION_NUMBER"),
                    "company": (product.get("NAME") or "").strip(),
                    "product": (product.get("TRADE_NAME") or "").strip(),
                    "product_category": (product.get("PRODUCT_CATEGORY") or "").strip(),
                    "issued": issued,
                    "expires": expires,
                    "pdf_url": PDF_BASE + (product.get("CERTIFICATION_NUMBER") or "") + ".pdf",
                    "source_url": "https://listings.nsf.org/certified-products-systems",
                })
    return rows


def fetch_companies():
    """Per-company address / phone / website, keyed by company name."""
    data = post("company/search/products", empty_search())
    out = {}
    for group in data.get("data") or []:
        info = group.get("companyInfo") or {}
        name = (info.get("companyName") or "").strip()
        if not name:
            continue
        out[name] = {
            "address": " ".join((info.get("companyAddress") or "").split()),
            "phone": " ".join((info.get("companyContact") or "").split()),
            "website": (info.get("companyURL") or "").strip() or None,
        }
    return out


# --------------------------------------------------------------------- PDF
def _person(email=None, name=None, org=None, source=None):
    return {"email": email or "", "name": name or "", "org": org or "", "source": source or ""}


def _split_person_line(line):
    """'Thomas P. Gloria, PhD (Chair), Industrial Ecology Consultants'."""
    em = EMAIL_RE.search(line)
    head = line[:em.start()].strip(" ,:-") if em else line
    parts = [p.strip() for p in head.split(",") if p.strip()]
    name = parts[0] if parts else ""
    org = parts[-1] if len(parts) > 1 else ""
    return (em.group(0) if em else ""), name, org


def _lines_after(text, position, limit=8):
    """The next few non-empty lines after a match position.

    A line that starts a new sentence ends the block. Without that, a
    preparer block runs on into the verifier's name and email, and the
    EPD looks like it was written by the verifier.
    """
    lines = []
    for raw in text[position:].split("\n")[:limit]:
        line = " ".join(raw.split())
        if not line:
            continue
        if lines and re.match(r"(?i)^(this |these |limitations|environmental product|"
                              r"declaration|product scope|declared product|references|"
                              r"glossary|data quality)", line):
            break
        lines.append(line)
    return lines


def _block_after(text, label, limit=8):
    """The block of lines following a field label, if the label is present."""
    m = re.search(re.escape(label), text, re.I)
    if not m:
        return []
    return _lines_after(text, m.end(), limit)


ORG_WORDS = re.compile(
    r"(?i)inc|llc|ltd|gmbh|consult|sustainab|engineer|material|associate|"
    r"corporat|group|institute|limited|company|technolog|partners|advisor|"
    r"laborator|universit|entprise|solutions|alliance|authority")
PERSONISH = re.compile(r"^[A-Z][\w.'-]+(?: [A-Z][\w.'-]*){1,3}$")


def _person_from_lines(lines, source):
    """Pull a person and organisation out of a block of PDF lines.

    The block may hold a name, a job title, an organisation, a postal
    address and an email, in any order, sometimes with a URL mixed in.
    """
    email = None
    org = None
    name = None
    previous = None
    for line in lines:
        found = EMAIL_RE.search(line)
        if found:
            if not email:
                email = found.group(0)
                before = line[:found.start()].strip(" ,;:-()")
                if before:
                    name = name or before
            continue
        if re.search(r"(?i)^https?://|^www\.|\.com\b|\.org\b|\.net\b", line):
            continue
        if re.match(r"^\d+\s|,\s*[A-Z]{2}\s+\d{5}|suite|street|road|avenue|parkway|"
                    r"drive|blvd|lane|highway", line, re.I):
            continue  # a postal address
        if re.search(r"\b\d{1,5}\s*$", line) and len(line) < 40:
            continue  # a street line ending in a house number
        if ORG_WORDS.search(line):
            if org is None:
                org = line
                if " - " in line and name is None:
                    head, _, tail = line.partition(" - ")
                    if PERSONISH.match(head.strip()):
                        name = head.strip()
                        org = tail.strip()
            previous = None
            continue
        if name is None and previous is None and PERSONISH.match(line):
            name = line
        previous = line
    if not (email or name or org):
        return None
    return _person(email, name, org, source)


def parse_pdf(pdf_bytes):
    """Pull the lead fields out of an EPD PDF.

    Field names differ between the NSF templates in circulation: the
    concrete template says "LCA Preparer", the coatings template says
    "Declaration Prepared by", the barrier template says "This Reference
    Life Cycle Assessment was Conducted in Accordance with ISO 14044 ...
    By:", and one template has no preparer field at all. All are handled.
    """
    import fitz  # PyMuPDF
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        text = "\n".join(page.get_text() for page in doc)

    out = {"emails": sorted(set(EMAIL_RE.findall(text)))}

    m = re.search(r"EPD Type[ \t]*\n[ \t]*([^\n]{3,60})", text, re.I)
    out["epd_type_stated"] = " ".join(m.group(1).split()) if m else None

    m = re.search(r"Declaration Holder[ \t]*\n[ \t]*([^\n]{3,90})", text, re.I)
    if not m:
        m = re.search(r"Manufacturer Name and Address[ \t]*\n[ \t]*([^\n]{3,90})", text, re.I)
    out["declaration_holder"] = " ".join(m.group(1).split()) if m else None

    # Some templates put the manufacturer's own email in the holder line.
    holder_email = None
    if out["declaration_holder"]:
        em = EMAIL_RE.search(out["declaration_holder"])
        if em:
            holder_email = em.group(0)
    out["holder_email"] = holder_email

    # --- who wrote it -------------------------------------------------
    # Each template labels the preparer differently, and the block below
    # the label varies in shape: a name, a name and an organisation, or
    # several names then an address then the email. So take the block and
    # pick the pieces out of it rather than assuming a fixed layout.
    preparer = None
    for label, source in (("LCA Preparer", "LCA Preparer"),
                          ("Declaration Prepared by", "Declaration Prepared by"),
                          ("Author of the Life Cycle Assessment:", "LCA author"),
                          ("LCA CONSULTANT", "LCA consultant")):
        lines = _block_after(text, label)
        if lines:
            preparer = _person_from_lines(lines, source)
            if preparer:
                break
    if preparer is None:
        # "This life cycle assessment was conducted in ... by:" or "... PCRs:",
        # then the authoring body. The label is broken across lines, so
        # search forward for the colon rather than expecting one line.
        m = re.search(r"assessment was conducted in", text, re.I)
        if m:
            tail = text[m.end():m.end() + 400]
            colon = re.search(r"(?:by|PCRs)[ \t]*:[ \t]*\n", tail, re.I)
            if colon:
                lines = _lines_after(text, m.end() + colon.end())
                if lines:
                    preparer = _person_from_lines(lines, "LCA author")
    out["preparer"] = preparer

    # --- third parties ------------------------------------------------
    # "This life cycle assessment was independently verified in accordance
    # with ISO 14044 and the reference PCR by:" then a name and an email.
    # The phrase is broken across lines in some templates, and some PDFs
    # verify the declaration against ISO 14025 first, so try each
    # occurrence until one yields a person.
    verifier = None
    for m in re.finditer(r"independently verified", text, re.I):
        tail = text[m.end():m.end() + 400]
        colon = re.search(r"by[ \t]*:[ \t]*\n", tail, re.I)
        if not colon:
            continue
        lines = _lines_after(text, m.end() + colon.end())
        candidate = _person_from_lines(lines, "ISO 14044 verification")
        if candidate and candidate.get("email"):
            verifier = candidate
            break
    out["verifier"] = verifier

    reviewer = None
    m = re.search(r"PCR review was conducted by[ \t]*:[ \t]*\n?[ \t]*([^\n]{2,160})", text, re.I)
    if m:
        email, name, org = _split_person_line(" ".join(m.group(1).split()))
        if email:
            reviewer = _person(email, name, org, "PCR review")
    out["pcr_reviewer"] = reviewer

    # --- the manufacturer's own contact --------------------------------
    contact = None
    m = re.search(
        r"For additional explanatory material[^\n]*\n[ \t]*([^\n]{2,60})\n[ \t]*"
        r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", text, re.I)
    if m:
        contact = _person(m.group(2), m.group(1).strip(), None, "EPD contact")
    out["company_contact"] = contact

    m = re.search(r"LCA Software[ \t]*&?[ \t]*Version(?: Number)?[ \t]*\n[ \t]*([^\n]{2,60})",
                  text, re.I)
    if not m:
        m = re.search(r"LCA Software and Version Number[ \t]*\n[ \t]*([^\n]{2,60})", text, re.I)
    out["lca_software"] = " ".join(m.group(1).split()) if m else None

    out["urls"] = sorted(set(BARE_URL_RE.findall(text)) | set(ANY_URL_RE.findall(text)))

    # A single named plant means the numbers are facility-level, which is
    # what California asks for, even where the template omits an EPD Type.
    facility = None
    m = re.search(r"(?i)manufactured at (?:its|the) facility located in[^\n]{0,140}", text)
    if not m:
        m = re.search(r"(?i)all production is completed at the [^\n]{0,140}", text)
    if not m:
        m = re.search(r"(?i)products produced by[^\n]{0,140}facility[^\n]{0,80}", text)
    if m:
        facility = m.group(0)
    out["facility_evidence"] = " ".join(facility.split()) if facility else None
    return out


# --------------------------------------------------------------- enrichment
def fetch_and_parse(record, cache_dir, refresh=False):
    number = record["epd_number"]
    cached = os.path.join(cache_dir, number + ".json")
    if os.path.exists(cached) and not refresh:
        with open(cached, encoding="utf-8") as fh:
            record.update(json.load(fh))
            return record
    try:
        parsed = parse_pdf(get_bytes(record["pdf_url"]))
    except Exception as exc:  # one dead PDF must not sink the run
        parsed = {"error": str(exc)[:120], "emails": []}
    os.makedirs(cache_dir, exist_ok=True)
    with open(cached, "w", encoding="utf-8") as fh:
        json.dump(parsed, fh, indent=1)
    record.update(parsed)
    return record


def load_overlay(path=None):
    """Manually verified company domains, with the URL each came from.

    NSF leaves companyURL null for many registrants and the PDFs only
    sometimes carry the company's own site. Where a domain was resolved
    another way it is recorded here with a source, so nothing in the lead
    list is unsourced.
    """
    path = path or os.path.join(SKILL, "references", "company-domains.csv")
    overlay = {}
    if not os.path.exists(path):
        return overlay
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            overlay[(row.get("company") or "").strip().lower()] = {
                "domain": (row.get("domain") or "").strip().lower(),
                "source": (row.get("source") or "").strip(),
            }
    return overlay


def name_tokens(company):
    stop = {"the", "inc", "llc", "ltd", "co", "corp", "company", "us", "usa",
            "and", "a", "of", "dba", "limited", "incorporated", "group"}
    return {t for t in re.split(r"[^a-z0-9]+", company.lower())
            if len(t) > 2 and t not in stop}


def resolve_domain(company, info, records, overlay):
    """The manufacturer's own domain, in order of trust.

    1. the NSF company record's website
    2. a manually verified overlay entry (references/company-domains.csv)
    3. a site named in the PDF whose domain matches the company name
    4. the domain of the manufacturer's own EPD contact

    Never a third party: the operator, the verifier, the consultant and
    the LCA author all put their own domain in these PDFs, and treating
    one as the company sends the lead to the wrong inbox.
    """
    site = domain_of((info or {}).get("website"))
    if site and not is_third_party(site):
        return site, "NSF company record"

    entry = overlay.get(company.strip().lower())
    if entry and entry["domain"]:
        return entry["domain"], "verified overlay"

    tokens = name_tokens(company)
    counts = Counter()
    for record in records:
        for url in record.get("urls") or []:
            domain = domain_of(url)
            if not domain or is_third_party(domain) or domain.endswith(".gov"):
                continue
            base = domain.split(".")[0]
            if base in tokens or any(t in base for t in tokens if len(t) > 3):
                counts[domain] += 3
    if counts:
        return counts.most_common(1)[0][0], "PDF company site"

    counts = Counter()
    for record in records:
        person = record.get("company_contact") or {}
        email = person.get("email")
        if email and not is_third_party(email) and not is_third_party_author(email):
            counts[domain_of(email)] += 1
    if counts:
        return counts.most_common(1)[0][0], "PDF company contact"
    return None, None


def first_party_emails(records, domain):
    """Emails on the manufacturer's own domain, plus its EPD contact."""
    found = set()
    for record in records:
        for email in record.get("emails") or []:
            if is_third_party(email) or is_third_party_author(email):
                continue
            if domain and domain_of(email) == domain:
                found.add(email)
        for person in (record.get("company_contact"), ):
            email = (person or {}).get("email")
            if email and not is_third_party(email) and not is_third_party_author(email):
                found.add(email)
        holder = record.get("holder_email")
        if holder and not is_third_party(holder) and not is_third_party_author(holder):
            found.add(holder)
    return sorted(found)


def classify_scope(record):
    """Facility, product, product-average or industry, plus the evidence.

    An unknown type stays unknown. California rejects anything that is not
    facility-specific, so an unknown type must not earn the bonus.
    """
    stated = record.get("epd_type_stated")
    low = (stated or "").lower()
    if "industry average" in low or "industry-wide" in low:
        return "industry", stated
    if "from 2 manufacturing" in low or "multi-plant" in low or "multiple plants" in low:
        return "product_average", stated
    if "facility" in low:
        return "facility", stated
    if record.get("facility_evidence"):
        return "facility", record["facility_evidence"]
    if "product specific" in low or "product-specific" in low:
        return "product", stated
    if stated:
        return "unknown", stated
    return "unknown", None


def classify_developer(record, domain):
    """Who wrote the EPD: in-house, a known consultant, an automated tool.

    In-house means the preparer sits on the manufacturer's own domain.
    Master Builders Solutions writes the concrete EPDs for several
    registrants in this list while selling them admixtures, so an EPD it
    authored is not an in-house declaration and the renewal will not run
    through the manufacturer.
    """
    preparer = record.get("preparer") or {}
    email = preparer.get("email")
    org = (preparer.get("org") or "")
    name = (preparer.get("name") or "")
    software = (record.get("lca_software") or "").lower()
    who = f"{name} {org}".lower()

    if email and domain and domain_of(email) == domain:
        return "in_house", preparer
    if email and is_consultant(email):
        return "consultant", preparer
    if any(marker in who for marker in CONSULTANT_ORGS):
        return "consultant", preparer
    if email and is_third_party_author(email):
        return "third_party_author", preparer
    if any(marker in who for marker in THIRD_PARTY_AUTHOR_NAMES):
        return "third_party_author", preparer
    if email and not is_third_party(email):
        return "third_party", preparer
    if not preparer.get("name") and any(word in software for word in AUTOMATED_SOFTWARE):
        return "automated", None
    if preparer.get("name"):
        return "third_party", preparer
    return "unknown", None


# ----------------------------------------------------------------- scoring
def score_company(records):
    """The issue rubric, applied once per manufacturer."""
    reasons = []
    total = 0

    live = [r for r in records if r["scope"] != "industry"]
    excluded = len(records) - len(live)
    if not live:
        return 0, ["excluded: industry-wide EPD only"], {}

    materials = {r["material"] for r in live}
    ca = {m for m in materials if MATERIALS.get(m, {}).get("ca")}
    states = set()
    for m in materials:
        states.update(MATERIALS.get(m, {}).get("states", []))

    facility = any(r["scope"] == "facility" for r in live)
    if ca and facility:
        total += 3
        reasons.append("+3 California Buy Clean material with a facility-specific EPD")
    elif ca:
        reasons.append("+0 California Buy Clean material, EPD is not facility-specific")

    if states:
        total += 2
        reasons.append("+2 material covered by another state's Buy Clean programme: "
                       + ", ".join(sorted(states)))

    if len(live) >= 5:
        total += 2
        reasons.append(f"+2 {len(live)} EPDs expiring")

    developers = {r["developer"] for r in live}
    if "in_house" in developers:
        total += 2
        reasons.append("+2 declaration prepared in-house (an LCA team to pitch)")
    elif "consultant" in developers:
        total -= 2
        reasons.append("-2 developed by a known consultant")
    elif "third_party_author" in developers:
        total -= 2
        reasons.append("-2 written by a third party that supplies the manufacturer")
    elif "automated" in developers:
        total -= 2
        reasons.append("-2 developed by an automated tool")
    if excluded:
        reasons.append(f"({excluded} industry-wide EPDs excluded)")

    facts = {
        "materials": ", ".join(sorted(materials)),
        "ca_buy_clean": ", ".join(sorted(ca)),
        "states": ", ".join(sorted(states)),
        "scope": ", ".join(sorted({r["scope"] for r in live})),
        "developer": ", ".join(sorted(d for d in developers if d != "unknown")) or "unknown",
    }
    return total, reasons, facts


# ------------------------------------------------------------------ output
CSV_COLUMNS = ["company", "score", "us_based", "epds_expiring", "earliest_expiry",
               "latest_expiry", "days_to_expiry", "contact_by", "timing", "materials",
               "ca_buy_clean", "buy_clean_states", "scope", "developer", "domain",
               "domain_source", "website", "phone", "address", "contacts",
               "lca_preparer", "lca_preparer_email", "epd_numbers", "reasons", "source_url"]


def days_until(date_text, today=None):
    import datetime
    today = today or datetime.date.today()
    try:
        year, month, day = (int(x) for x in date_text.split("-"))
    except (AttributeError, ValueError):
        return None
    return (datetime.date(year, month, day) - today).days


def timing_for(days):
    """Where the company sits against the 9-to-12-month contact window.

    California requires the EPD to be valid at installation, not at bid,
    so the contact window opens 12 months before expiry. Anything inside
    9 months is already late.
    """
    if days is None:
        return "unknown"
    if days < 0:
        return "expired"
    if days <= 270:
        return "overdue - contact now"
    if days <= 365:
        return "contact now"
    return "later"


def shift_months(date_text, months):
    """Move a YYYY-MM-DD date back by N months."""
    year, month, day = (int(x) for x in date_text.split("-"))
    total = (year * 12 + month - 1) - months
    year, month = total // 12, total % 12 + 1
    last = [31, 29 if (year % 4 == 0 and (year % 100 or year % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return f"{year:04d}-{month:02d}-{min(day, last):02d}"


def california_watch(listings):
    """Every California Buy Clean material EPD in the registry, by expiry.

    The +3 California rule only fires when a CA material is in the window.
    NSF's registry carries thermal insulation and no flat glass, rebar or
    structural steel category, so the CA material EPDs expire on their own
    schedule. Listing them all here means the gap is visible rather than
    looking like a scoring failure.
    """
    rows = []
    for record in listings:
        category = (record.get("product_category") or "").lower()
        material = "other"
        for needle, value in CATEGORY_MATERIAL.items():
            if needle in category:
                material = value
                break
        if not MATERIALS.get(material, {}).get("ca"):
            continue
        rows.append({
            "epd_number": record["epd_number"],
            "company": record["company"],
            "product_category": record["product_category"],
            "expires": record["expires"],
            "source_url": "https://listings.nsf.org/certified-products-systems",
        })
    rows.sort(key=lambda r: (r["expires"] or ""))
    return rows


def build(start, end, cache_dir, refresh, with_pdfs, overlay):
    listings = fetch_listings()
    companies = fetch_companies()
    window = [r for r in listings if r["expires"] and start <= r["expires"] <= end]
    watch = california_watch(listings)
    if not window:
        return [], [], watch

    if with_pdfs:
        with concurrent.futures.ThreadPoolExecutor(6) as pool:
            window = list(pool.map(
                lambda r: fetch_and_parse(r, cache_dir, refresh), window))

    by_company = {}
    for record in window:
        category = (record.get("product_category") or "").lower()
        material = "other"
        for needle, value in CATEGORY_MATERIAL.items():
            if needle in category:
                material = value
                break
        record["material"] = material
        record["scope"], record["scope_evidence"] = classify_scope(record)
        by_company.setdefault(record["company"], []).append(record)

    rows = []
    for company, records in by_company.items():
        info = companies.get(company) or {}
        domain, domain_source = resolve_domain(company, info, records, overlay)
        for record in records:
            record["domain"] = domain
            record["developer"], _ = classify_developer(record, domain)
        total, reasons, facts = score_company(records)
        contacts = first_party_emails(records, domain)
        preparers = [r["preparer"] for r in records if r.get("preparer")]
        preparer = preparers[0] if preparers else {}
        earliest = min(r["expires"] for r in records)
        days = days_until(earliest)
        rows.append({
            "company": company,
            "score": total,
            "us_based": "yes" if is_us_address(info.get("address")) else "no",
            "epds_expiring": len(records),
            "earliest_expiry": earliest,
            "latest_expiry": max(r["expires"] for r in records),
            "days_to_expiry": days if days is not None else "",
            "contact_by": shift_months(earliest, 12),
            "timing": timing_for(days),
            "materials": facts.get("materials", ""),
            "ca_buy_clean": facts.get("ca_buy_clean", ""),
            "buy_clean_states": facts.get("states", ""),
            "scope": facts.get("scope", ""),
            "developer": facts.get("developer", ""),
            "domain": domain or "",
            "domain_source": domain_source or "",
            "website": info.get("website") or "",
            "phone": info.get("phone") or "",
            "address": info.get("address") or "",
            "contacts": ", ".join(contacts),
            "lca_preparer": preparer.get("name", ""),
            "lca_preparer_email": preparer.get("email", "") or "",
            "epd_numbers": ", ".join(r["epd_number"] for r in records),
            "reasons": " | ".join(reasons),
            "source_url": "https://listings.nsf.org/certified-products-systems",
        })
    rows.sort(key=lambda r: (r["us_based"] != "yes", -r["score"], r["earliest_expiry"]))
    return rows, window, watch


def write_csv(rows, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in CSV_COLUMNS})
    return path


def write_handoff(rows, path, limit=50, us_only=True):
    """Company domains for the find-cold-leads skill.

    That skill takes a list of company domains and a theme, and returns
    named people. The PDF contact travels with each domain, because a
    named sustainability contact from the EPD is usually a better lead
    than anything a people search returns.
    """
    picked = [r for r in rows if r.get("domain") and (r.get("us_based") == "yes" or not us_only)]
    picked = picked[:limit]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["company", "domain", "score", "timing", "earliest_expiry",
                         "contacts", "materials", "reasons"])
        for row in picked:
            writer.writerow([row.get(k, "") for k in
                             ("company", "domain", "score", "timing", "earliest_expiry",
                              "contacts", "materials", "reasons")])
    return path, len(picked)


def write_xlsx(rows, details, watch, path):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()
    sheet = book.active
    sheet.title = "Leads"
    sheet.append(CSV_COLUMNS)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="404040")
    for row in rows:
        sheet.append([row.get(k, "") for k in CSV_COLUMNS])
    widths = {"company": 36, "score": 7, "us_based": 9, "materials": 16,
              "ca_buy_clean": 14, "buy_clean_states": 18, "scope": 16, "developer": 18,
              "domain": 22, "domain_source": 20, "website": 26, "phone": 15,
              "address": 40, "contacts": 34, "lca_preparer": 20,
              "lca_preparer_email": 28, "epd_numbers": 40, "reasons": 90,
              "source_url": 40, "earliest_expiry": 15, "latest_expiry": 15,
              "contact_by": 13, "epds_expiring": 13, "days_to_expiry": 13,
              "timing": 20}
    for index, name in enumerate(CSV_COLUMNS, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = widths.get(name, 14)
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=cell.column in (16, 17, 18))
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions

    detail = book.create_sheet("EPDs")
    detail_cols = ["epd_number", "company", "domain", "product", "product_category",
                   "material", "issued", "expires", "scope", "scope_evidence",
                   "developer", "declaration_holder", "lca_preparer", "lca_preparer_email",
                   "verifier", "pcr_reviewer", "company_contact", "lca_software",
                   "emails", "pdf_url"]
    detail.append(detail_cols)
    for cell in detail[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="404040")
    for record in sorted(details, key=lambda r: (r["company"], r["expires"])):
        prep = record.get("preparer") or {}
        ver = record.get("verifier") or {}
        rev = record.get("pcr_reviewer") or {}
        con = record.get("company_contact") or {}
        detail.append([
            record.get("epd_number", ""), record.get("company", ""),
            record.get("domain", ""), record.get("product", ""),
            record.get("product_category", ""), record.get("material", ""),
            record.get("issued", ""), record.get("expires", ""),
            record.get("scope", ""), record.get("scope_evidence", ""),
            record.get("developer", ""), record.get("declaration_holder", "") or "",
            prep.get("name", ""), prep.get("email", "") or "",
            ver.get("email", "") or "", rev.get("email", "") or "",
            con.get("email", "") or "", record.get("lca_software", "") or "",
            ", ".join(record.get("emails") or []), record.get("pdf_url", ""),
        ])
    for index, name in enumerate(detail_cols, start=1):
        detail.column_dimensions[get_column_letter(index)].width = 30 if name in (
            "product", "declaration_holder", "lca_preparer", "lca_preparer_email",
            "verifier", "pcr_reviewer", "company_contact", "emails", "scope_evidence",
            "pdf_url") else 14
    detail.freeze_panes = "A2"

    ca = book.create_sheet("California watch")
    ca_cols = ["epd_number", "company", "product_category", "expires", "source_url"]
    ca.append(ca_cols)
    for cell in ca[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="404040")
    for record in watch:
        ca.append([record.get(k, "") for k in ca_cols])
    for index, name in enumerate(ca_cols, start=1):
        ca.column_dimensions[get_column_letter(index)].width = 40 if name == "product_category" else 18
    ca.freeze_panes = "A2"

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    book.save(path)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-11-01", help="expiry window start")
    parser.add_argument("--end", default="2027-10-31", help="expiry window end")
    parser.add_argument("--out", default="reports/epd-expiry-leads.xlsx")
    parser.add_argument("--csv", default=None, help="also write a CSV next to the xlsx")
    parser.add_argument("--handoff", default=None,
                        help="write a company-domain CSV for the find-cold-leads skill")
    parser.add_argument("--handoff-limit", type=int, default=50,
                        help="how many companies in the handoff file")
    parser.add_argument("--cache-dir", default="data/pdf-cache")
    parser.add_argument("--overlay", default=None, help="verified company domains CSV")
    parser.add_argument("--refresh", action="store_true", help="re-download cached PDFs")
    parser.add_argument("--no-pdfs", action="store_true", help="skip PDF parsing")
    args = parser.parse_args(argv)

    rows, details, watch = build(args.start, args.end, args.cache_dir, args.refresh,
                                 not args.no_pdfs, load_overlay(args.overlay))
    xlsx = write_xlsx(rows, details, watch, args.out)
    print(f"companies: {len(rows)}   EPDs in window: {len(details)}   -> {xlsx}")
    print(f"California Buy Clean material EPDs in the registry: {len(watch)}")
    for row in rows:
        print(f"  {row['score']:>3} {row['us_based']:<3} {row['company'][:32]:<32} "
              f"{row['epds_expiring']:>2} EPD  {row['earliest_expiry']}  "
              f"{(row['domain'] or '-'):<24} {row['developer']:<18} {row['contacts'][:34]}")
    if args.csv:
        print("csv:", write_csv(rows, args.csv))
    if args.handoff:
        path, count = write_handoff(rows, args.handoff, limit=args.handoff_limit)
        print(f"handoff: {count} company domains -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
