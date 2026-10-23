#!/usr/bin/env python3
"""Read the Caltrans EPD Authorized Materials List.

<https://mets.dot.ca.gov/aml/EnvironmentalProductDeclarations.php> is a
plain HTML table: company, facility address, declaration number, and the
EPD validity range. It is the state's own list of the EPDs that qualify
for Buy Clean California Act work, and it carries the exact dates the
lead list needs.

This is where the California Buy Clean materials live. NSF's registry has
no flat glass, rebar or structural steel product category at all, and
Smart EPD's data is EC3's, so the steel and rebar EPDs that Caltrans
actually accepts were unreadable until this page turned up.

What the page does not give is a person, a website or an email. The
contact has to come from a people search on the company domain, and this
module only claims the fields it can read.

Terms: Caltrans' conditions of use say that information on the site is
"considered in the public domain" and "may be distributed or copied as
"permitted by law", with no clause against automated retrieval, and there
is no robots.txt on mets.dot.ca.gov. The list is published for
contractors and manufacturers to use.

Usage:
    python caltrans_aml.py                       # every live row, as JSON
    python caltrans_aml.py --start 2026-11-01 --end 2027-10-31
"""
import argparse
import csv
import datetime
import html
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)

AML_URL = "https://mets.dot.ca.gov/aml/EnvironmentalProductDeclarations.php"
UA = "Mozilla/5.0 (compatible; epd-expiry-leads/1.0)"

# The page's own stylesheet strikes through rows whose EPD has expired,
# and puts superseded rows in a separate "Not Valid for projects awarded
# after 12/31/2024" table section. Both are dead rows, and a dead row is
# not a lead: nobody renews an EPD that is no longer accepted.
EXPIRED_CLASS = "expiredclass"

# The Buy Clean materials, mapped from the material categories Caltrans
# publishes. Flat glass and mineral wool board insulation are also BCCA
# materials, but Caltrans does not list them here: flat glass is
# "informational" and its EPD goes to the Engineer, and mineral wool
# board is not on this list. Both are still gaps.
CATEGORY_MATERIAL = {
    "carbon steel rebar": "rebar",
    "structural steel : plate": "structural steel",
    "structural steel: plate": "structural steel",
    "structural steel: hollow structural sections": "structural steel",
    "structural steel: hot-rolled sections": "structural steel",
}


def _get(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _cells(row_html):
    cells = re.findall(r"<t[hd].*?</t[hd]>", row_html, re.S | re.I)
    out = []
    for cell in cells:
        text = html.unescape(re.sub(r"<[^>]+>", " ", cell))
        out.append(" ".join(text.split()))
    return out


def _parse_date(text):
    """'1/1/2027' -> date(2027, 1, 1). Caltrans dates are US month first."""
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", (text or "").strip())
    if not m:
        return None
    return datetime.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))


def parse_aml(page_html):
    """Every row of the AML table, with its category and validity range.

    Returns one dict per declaration at one facility. A company with
    several mills has several rows, which is what the list is: an EPD
    is accepted for a facility, not for a company.
    """
    table = re.search(r"<table.*?</table>", page_html, re.S | re.I)
    if not table:
        return []
    rows = []
    category = ""
    for row_html in re.findall(r"<tr.*?</tr>", table.group(0), re.S | re.I):
        cells = _cells(row_html)
        joined = " | ".join(c for c in cells if c)
        if not joined:
            continue
        if "Material Category" in joined:
            category = joined.replace("Material Category:", "").strip()
            continue
        if len(cells) < 4:
            continue
        validity = re.match(
            r"(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})", cells[3])
        if not validity:
            continue
        expired = EXPIRED_CLASS in row_html.lower()
        # A superseded row sits in a section marked "Not Valid for
        # projects awarded after 12/31/2024", whether or not the row
        # itself is struck through.
        superseded = "not valid" in category.lower()
        material = CATEGORY_MATERIAL.get(category.lower(), "other")
        rows.append({
            "source": "Caltrans AML",
            "category": category,
            "material": material,
            "company": cells[0],
            "facility_address": cells[1],
            "declaration_number": cells[2],
            "valid_from": _parse_date(validity.group(1)),
            "valid_to": _parse_date(validity.group(2)),
            "expired": expired,
            "superseded": superseded,
            # Caltrans accepts these for Buy Clean work, which means the
            # EPD reports facility-specific GWP for the facility named
            # here. That is what the state needs, and the state says so
            # by listing the facility address against the declaration.
            "scope": "facility",
            "source_url": AML_URL,
        })
    return rows


def fetch_aml():
    return parse_aml(_get(AML_URL))


def live_rows(rows, start=None, end=None):
    """Rows that are still accepted and still in the window.

    Two things disqualify a row. An expired EPD is not a renewal
    opportunity, it is a dead one. And a row the state has marked
    "not valid for projects awarded after 12/31/2024" has been
    superseded, so whoever replaced it is the lead, not the holder of
    the old declaration.
    """
    out = []
    for row in rows:
        if row["expired"] or row["superseded"]:
            continue
        if row["valid_to"] is None:
            continue
        if start and row["valid_to"] < start:
            continue
        if end and row["valid_to"] > end:
            continue
        out.append(row)
    return out


def parent_company(name):
    """The company behind a facility name, for per-company scoring.

    Caltrans lists the mill, not the group: "Nucor Steel, Incorporated -
    Auburn" is one of Nucor's rebar mills, and the pitch is to Nucor.
    The trailing dash and what follows it is the mill, except where the
    part after the dash is a division rather than a place.

    The dash has to have a space before it. "Nucor-Yamato Steel - Arkansas"
    is one company's name, and splitting on any hyphen would turn it into
    "Nucor" and merge a separate company into Nucor Steel.
    """
    name = " ".join(name.strip().split())
    m = re.search(r"\s+-\s*", name)
    if not m:
        return name
    head = name[:m.start()].strip()
    return head or name


# Caltrans names the same company two ways in different material
# sections, and a lead list that lists Nucor twice is a lead list
# nobody trusts. Keys are the lower-cased parent name.
COMPANY_ALIASES = {
    "nucor steel": "Nucor Steel, Incorporated",
    "nucor steel, incorporated": "Nucor Steel, Incorporated",
}


def canonical_company(name):
    """The company to score and to list, after the mill name is dropped."""
    parent = parent_company(name)
    return COMPANY_ALIASES.get(parent.lower(), parent)


def group_by_company(rows):
    """Rows grouped under the company that owns the mill.

    A parent gets one entry with all its facilities. The score applies
    once per company, so the grouping has to happen before scoring or
    one company gets counted once per mill.
    """
    companies = {}
    for row in rows:
        parent = canonical_company(row["company"])
        companies.setdefault(parent, []).append(row)
    return companies


def declarations():
    """The declarations whose documents have been read, keyed by family.

    The Caltrans list gives a declaration number but no author, so who
    wrote the declaration is only known by reading the document. The
    documents for the HSS producers are published by the Steel Tube
    Institute at <https://steeltubeinstitute.org/hollow-structural-sections/hss-facility-specific-epds/>
    and by the manufacturers themselves, so they are readable.

    UL revises declarations, and the number in the state's list is not
    always the number on the current document: the Caltrans row for Vest
    carries 4790434237.101.1 and the document is 4790434237.101.3. So
    the lookup is on the declaration family, the number without its
    revision suffix, and the first two parts of the number.
    """
    path = os.path.join(SKILL, "references", "declarations.csv")
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            number = (row.get("declaration") or "").strip()
            if not number:
                continue
            out[_family(number)] = row
    return out


def _family(number):
    """The declaration family, without the revision suffix."""
    parts = number.split(".")
    return ".".join(parts[:2]) if len(parts) > 2 else number


def lead_records(start=None, end=None, page_html=None):
    """AML rows in the shape the lead list expects.

    The scope is facility because Caltrans lists the facility address
    against the declaration. The declaration may still be a companywide
    EPD with per-facility GWP tables, which is the form DGS accepts, and
    the number of facilities a declaration covers is carried on the
    record so the lead sheet can say so.
    """
    rows = fetch_aml() if page_html is None else parse_aml(page_html)
    live = live_rows(rows, start, end)
    by_declaration = {}
    for row in live:
        by_declaration.setdefault(row["declaration_number"], set()).add(
            row["facility_address"])

    known = declarations()
    out = []
    for row in live:
        record = {
            "source": "Caltrans AML",
            "epd_number": row["declaration_number"],
            "company": canonical_company(row["company"]),
            "mill": row["company"],
            "product": row["category"],
            "product_category": row["category"],
            "material": row["material"],
            "issued": row["valid_from"].isoformat() if row["valid_from"] else "",
            "expires": row["valid_to"].isoformat(),
            "facility_address": row["facility_address"],
            "facilities_for_declaration": len(
                by_declaration[row["declaration_number"]]),
            "address": row["facility_address"],
            # The evidence for facility scope is the state's own listing.
            "facility_evidence": "Caltrans Authorized Materials List: "
                                 + row["facility_address"],
            "pdf_url": "",
            "source_url": AML_URL,
        }
        doc = known.get(_family(row["declaration_number"]))
        if doc:
            record["preparer"] = {
                "email": (doc.get("contact_email") or "").strip(),
                "name": (doc.get("preparer_name") or "").strip(),
                "org": (doc.get("preparer_org") or "").strip(),
                "source": "EPD document",
            }
            record["declaration_document"] = doc.get("source_url", "")
            record["declaration_note"] = doc.get("note", "")
            record["pdf_url"] = doc.get("source_url", "")
        out.append(record)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", help="window start, YYYY-MM-DD")
    parser.add_argument("--end", help="window end, YYYY-MM-DD")
    parser.add_argument("--out", help="write JSON here")
    args = parser.parse_args(argv)

    start = datetime.date.fromisoformat(args.start) if args.start else None
    end = datetime.date.fromisoformat(args.end) if args.end else None
    records = lead_records(start, end)
    text = json.dumps(records, indent=1, ensure_ascii=False)
    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"{len(records)} rows -> {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
