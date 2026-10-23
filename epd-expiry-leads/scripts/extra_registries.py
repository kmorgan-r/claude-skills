#!/usr/bin/env python3
"""Read EPD Hub and EPD Portal.

Two registries the NSF script cannot reach, for different reasons.

**EPD Hub** (<https://manage.epdhub.com>) is the public library of EPD Hub
BV, an ISO 14025 programme operator in Amsterdam. It is a different
organisation from Building Transparency, so EC3's licence does not govern
it, and its terms are the most permissive of any registry in this skill.
The listing is the site's own JSON endpoint:

    GET https://manage.epdhub.com/?request_type=ajax&page=1&page_size=100

Date filters are ISO ``YYYY-MM-DD`` and ``date_valid`` is a comma-separated
range. The ``country`` filter is broken server-side - it returns zero rows
for the United States while the catalogue holds 101 of them - so the country
test is done here instead.

The EPDs are EN 15804 documents in two templates. The newer one names the
author ("EPD Author Prabhu Megharaj (LuxWall Inc.)") and the validity
("Date of Validity: 20.09.2027"); the older one names the software
instead ("Created with One Click LCA") and the validity in prose
("valid until 13.09.2027"). Both are handled here. The author field is
what the rubric's in-house rule needs, and EPD Hub is the only registry in
this skill that publishes it for a non-NSF declaration.

**EPD Portal** (<https://epdportal.org>) is a multi-source search run by
Multicert, indexing eleven European operators. It is the friendliest source
to build on and it documents itself for exactly this use:

    GET https://data.epdportal.org/api/search?geo=US&limit=25&offset=0

Two limits make it a second pass rather than a first:

- Validity is a **year**, not a date. ``valid_until: 2031`` where NSF
  gives a day. A lead list that ranks by days-to-expiry needs the exact
  date, so a Portal row can only flag "expires in 2027, go and get the
  exact date at source".
- The US records carry **no manufacturer**. ``producent`` is null for all
  77 of them, so the company has to come from the operator's own page.
  The operator pages are JavaScript applications with no usable API, and
  the same EPD is indexed twice, once under ÖKOBAUDAT and once under IBU,
  so the rows must be deduplicated on ``reg_no``.

Usage:
    python extra_registries.py --source epdhub
    python extra_registries.py --source epdportal --start 2026-11-01 --end 2027-10-31
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; epd-expiry-leads/1.0)"

HUB_LISTING = "https://manage.epdhub.com/?request_type=ajax&page={page}&page_size={size}"
PORTAL_API = "https://data.epdportal.org/api/search"

# The US, however the registry spells it.
US_COUNTRY = re.compile(r"united states|^usa$|united states of america", re.I)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"(?:https?://)?(?:www\.)?[A-Za-z0-9.-]+\.[A-Za-z]{2,10}")


def _get(url, headers=None, timeout=90, tries=3):
    """GET with retries. These registries 502 under a burst."""
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised
            last = exc
            time.sleep(2 * (attempt + 1))
    raise last


def _get_json(url, headers=None, **kw):
    return json.loads(_get(url, headers=headers, **kw).decode("utf-8", "replace"))


# ------------------------------------------------------------------ EPD Hub
def fetch_epdhub(country=None, date_from=None, date_to=None, page_size=100):
    """Every EPD Hub listing, or just the ones matching the filters.

    The server-side date filter is used when given, because pulling the
    whole 7,500-row catalogue on every run is 75 requests. The country
    filter is not used even when given, because it is broken.
    """
    query = []
    if date_from and date_to:
        query.append("date_valid=" + urllib.parse.quote(f"{date_from},{date_to}"))
    rows = []
    page = 1
    while page <= 200:
        url = HUB_LISTING.format(page=page, size=page_size)
        if query:
            url += "&" + "&".join(query)
        payload = _get_json(url, headers={"User-Agent": UA, "Accept": "application/json",
                                          "X-Requested-With": "XMLHttpRequest"})
        batch = payload.get("results") or []
        if not batch:
            break
        rows.extend(batch)
        page += 1
        time.sleep(0.3)
    if country:
        rows = [r for r in rows if US_COUNTRY.match(str(r.get("country") or "").strip())
                or "united states" in str(r.get("country") or "").lower()]
    return rows


def hub_date(text):
    """'26.01.2027' -> date(2027, 1, 26). EPD Hub writes dates day-first."""
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", str(text or "").strip())
    if not m:
        return None
    return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))


def _hub_text(pdf_bytes):
    import fitz  # PyMuPDF
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def _hub_validity(text):
    """The end of the validity period, from either template.

    The newer template writes "Date of Validity: 20.09.2027" and the
    older one "valid until 13.09.2027" or "valid until 14 July 2027".
    The listing's own date_valid field runs a day behind the document,
    so the document is the better source.
    """
    m = re.search(r"Date of Validity:\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if m:
        return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))

    m = re.search(r"valid until\s+(.{0,30})", text, re.I)
    if m:
        tail = m.group(1)
        # The date is written with the day and month sometimes split by a
        # space, so the spaces come out before the parts are read.
        compact = re.sub(r"[.\s]+", " ", tail)
        m = re.search(r"^(\d{1,2})\s+(\d{1,2})\s+(\d{4})", compact)
        if m:
            return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        m = re.search(r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", compact)
        if m:
            months = {mth.lower(): i for i, mth in enumerate(
                ["January", "February", "March", "April", "May", "June", "July",
                 "August", "September", "October", "November", "December"], 1)}
            month = months.get(m.group(2).lower())
            if month:
                return datetime.date(int(m.group(3)), month, int(m.group(1)))
    return None


def parse_hub_pdf(pdf_bytes):
    """The lead fields out of an EPD Hub PDF.

    The author field is the one that matters and the one that is not
    always there. The newer template names the person and their
    organisation; the older one names the software that produced the
    declaration and leaves the author implicit, which the rubric treats
    as an automated tool.
    """
    text = _hub_text(pdf_bytes)
    flat = " ".join(text.split())

    out = {"emails": sorted(set(EMAIL_RE.findall(text)))}

    m = re.search(r"EPD Author\s+(.+?)\s+EPD Verification", flat, re.I)
    if not m:
        m = re.search(r"EPD Author\s+(.+?)\s*$", flat, re.I)
    if m:
        author = m.group(1).strip()
        org = ""
        org_match = re.search(r"\((.+?)\)\s*$", author)
        if org_match:
            org = org_match.group(1).strip()
            author = author[:org_match.start()].strip()
        out["author_name"] = author
        out["author_org"] = org
    else:
        out["author_name"] = ""
        out["author_org"] = ""

    m = re.search(r"Created with\s+(.+?)(?:\s+EPD HUB|\s+ENVIRONMENTAL|$)", flat, re.I)
    out["lca_software"] = " ".join(m.group(1).split()) if m else None

    m = re.search(r"(?:E-mail|Contact details)\s+([^\s]+@[^\s]+)", flat, re.I)
    out["contact_email"] = m.group(1) if m else ""

    m = re.search(r"Manufacturer\s+(.+?)\s+Address", flat)
    out["manufacturer"] = " ".join(m.group(1).split()) if m else ""

    m = re.search(r"Address\s+(.+?)\s+(?:E-mail|Contact details)", flat)
    out["address"] = " ".join(m.group(1).split()) if m else ""

    m = re.search(r"Website\s+(\S+)", flat)
    out["website"] = m.group(1) if m else ""

    m = re.search(r"Place of production\s+(.+?)\s+Place\(s\) of installation", flat)
    out["place_of_production"] = " ".join(m.group(1).split()) if m else ""

    m = re.search(r"Averaging in EPD\s+(No [Gg]rouping|No averaging)", flat)
    out["averaging"] = " ".join(m.group(1).split()) if m else ""

    expires = _hub_validity(flat)
    # A string, not a date, so the parse result can be cached as JSON.
    out["expires"] = expires.isoformat() if expires else ""

    # The document cites standards bodies and academic papers, so the
    # URL list is filtered to domains that could be a manufacturer's own
    # site. A registry only needs the one it names, so the website field
    # is what the lead list actually uses.
    out["urls"] = sorted({u if u.startswith("http") else "https://" + u
                          for u in URL_RE.findall(text)
                          if not re.search(r"@|\.pdf|\.xlsx?$", u, re.I)
                          and re.search(r"\.(com|net|org|co|uk|us|io|de|eu|"
                                        r"fr|it|nl|es|ca|se|dk|fi|no|pl|jp|cn)$",
                                        u.strip("/").lower())})
    return out


def hub_scope(parsed):
    """The scope of an EPD Hub declaration, from what the PDF says.

    "No grouping" means the declaration is not an average across a group
    of plants, and a place of production that names a site rather than a
    state means it is facility-level. A place of production that names
    only a state or a country is not a single site, so it stays at
    product level: California will not accept it.
    """
    place = (parsed.get("place_of_production") or "").strip()
    if not place:
        return "unknown", None
    evidence = "Place of production " + place
    parts = [p.strip() for p in place.split(",") if p.strip()]
    if re.search(r"\d", place):
        return "facility", evidence          # a street address
    if len(parts) >= 2 and not _is_country(parts[-1]):
        return "facility", evidence          # city and state
    return "product", evidence               # a state, or a country


def _is_country(part):
    return part.lower() in ("usa", "u.s.a.", "united states",
                             "united states of america", "united kingdom")


def hub_records(rows, parse=None):
    """EPD Hub listings in the shape the lead list expects.

    ``parse`` is called with the PDF bytes and the EPD number, and
    returns the parsed fields, so the caller can cache the download and
    the parse together. Without it the listing fields are all there is.
    """
    out = []
    for row in rows:
        valid_to = hub_date(row.get("date_valid"))
        if not valid_to:
            continue
        pdf = ""
        for entry in row.get("epds") or []:
            path = entry.get("url") or ""
            if path:
                pdf = "https://manage.epdhub.com" + path
                break
        record = {
            "source": "EPD Hub",
            "epd_number": row.get("epd_number") or "",
            "company": (row.get("manufacturer") or "").strip(),
            "product": (row.get("product_name") or "").strip(),
            "product_category": (row.get("product_type") or row.get("category") or "").strip(),
            "issued": (row.get("date_published") or "").strip(),
            "expires": valid_to.isoformat(),
            "pdf_url": pdf,
            "source_url": "https://manage.epdhub.com/",
            "country": (row.get("country") or "").strip(),
        }
        if parse is not None and pdf:
            try:
                parsed = parse(pdf, record["epd_number"])
            except Exception as exc:  # one dead document must not sink the run
                parsed = {"error": str(exc)[:120]}
            record.update(parsed)
            if parsed.get("expires"):
                record["expires"] = parsed["expires"]
            if parsed.get("error"):
                record["pdf_ok"] = False
                out.append(record)
                continue
            scope, evidence = hub_scope(parsed)
            record["scope"] = scope
            record["scope_evidence"] = evidence
        out.append(record)
    return out


# -------------------------------------------------------------- EPD Portal
def fetch_epdportal(geo="US", limit=25, page_cap=200):
    """Every EPD Portal record for a country, deduplicated on reg_no.

    The same EPD is indexed once per operator that carries it, so a
    US record from a European programme operator appears twice. Deduping
    on ``reg_no`` collapses the duplicates.
    """
    seen = {}
    offset = 0
    while offset < limit * page_cap:
        url = (PORTAL_API + "?" +
               urllib.parse.urlencode({"geo": geo, "limit": limit, "offset": offset}))
        payload = _get_json(url, headers={"User-Agent": UA, "Accept": "application/json"})
        items = payload.get("items") or []
        if not items:
            break
        for item in items:
            key = item.get("reg_no") or item.get("uuid")
            seen.setdefault(key, item)
        if len(seen) >= (payload.get("total") or 0):
            break
        offset += limit
        time.sleep(0.3)
    return list(seen.values())


def portal_records(rows):
    """EPD Portal records in the shape the lead list expects.

    Validity is a year, so ``expires`` is the last day of that year and
    ``date_precision`` says so. The manufacturer is missing, which is
    why these rows are a second pass: they say a company has an EPD
    expiring, and the operator's page says which company.
    """
    out = []
    for row in rows:
        year = row.get("valid_until")
        if not year:
            continue
        try:
            year = int(year)
        except (TypeError, ValueError):
            continue
        out.append({
            "source": "EPD Portal",
            "epd_number": row.get("reg_no") or row.get("uuid") or "",
            "company": (row.get("producent") or "").strip(),
            "product": (row.get("name") or "").strip(),
            "product_category": (row.get("kategoria") or "").strip(),
            "operator": row.get("operator") or "",
            "issued": "",
            "expires": datetime.date(year, 12, 31).isoformat(),
            "date_precision": "year",
            "pdf_url": "",
            "source_url": row.get("source_url") or "",
        })
    return out


def portal_second_pass(rows, start=None, end=None):
    """The EPD Portal rows a human has to finish by hand.

    No manufacturer, and validity in years rather than days, so these
    cannot be scored or ranked. What they are good for is telling a
    person which declarations to go and look up, and at which operator.

    The window is matched on the year, not on a date. A row that is
    valid until some day in 2027 is inside a 2026-11 to 2027-10 window
    even though its year-end date is not.
    """
    out = []
    for row in portal_records(rows):
        year = int(row["expires"][:4])
        if start and year < int(str(start)[:4]):
            continue
        if end and year > int(str(end)[:4]):
            continue
        out.append({
            "epd_number": row["epd_number"],
            "operator": row["operator"],
            "product": row["product"],
            "expires_year": year,
            "manufacturer": "",
            "source_url": row["source_url"],
        })
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["epdhub", "epdportal", "portal-second-pass"],
                        required=True)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    if args.source == "epdhub":
        rows = hub_records(fetch_epdhub(country="US", date_from=args.start,
                                        date_to=args.end))
    elif args.source == "epdportal":
        rows = portal_records(fetch_epdportal())
        if args.start:
            start = datetime.date.fromisoformat(args.start)
            end = datetime.date.fromisoformat(args.end) if args.end else None
            rows = [r for r in rows
                    if end is None or start <= datetime.date.fromisoformat(r["expires"]) <= end]
    else:
        rows = portal_second_pass(fetch_epdportal(), args.start, args.end)

    text = json.dumps(rows, indent=1, ensure_ascii=False)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"{len(rows)} rows -> {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
