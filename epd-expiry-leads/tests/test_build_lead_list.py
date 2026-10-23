"""Checks for the EPD expiry lead list builder.

Run with: python tests/test_build_lead_list.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import build_lead_list as b
import caltrans_aml


def test_parse_period_is_month_first():
    """The certification period is US month-first. Reading it as day-first
    silently selects the wrong rows, so this is checked directly."""
    assert b.parse_period("06/24/2022 - 06/24/2027") == ("2022-06-24", "2027-06-24")
    assert b.parse_period("11/19/2021 - 11/19/2026") == ("2021-11-19", "2026-11-19")
    assert b.parse_period("01/31/2025 - 01/31/2030") == ("2025-01-31", "2030-01-31")
    # A day that cannot be a month proves the order.
    assert b.parse_period("12/30/2026 - 12/30/2031")[1] == "2031-12-30"
    assert b.parse_period("no date here") == (None, None)
    assert b.parse_period(None) == (None, None)


def test_classify_scope():
    assert b.classify_scope({"epd_type_stated": "Product Specific"})[0] == "product"
    assert b.classify_scope({"epd_type_stated": "Facility Specific"})[0] == "facility"
    assert b.classify_scope(
        {"epd_type_stated": "Manufacturer specific, Product specific"})[0] == "product"
    assert b.classify_scope(
        {"epd_type_stated": "Product Average (from 2 manufacturing plants)"})[0] == \
        "product_average"
    assert b.classify_scope({"epd_type_stated": "Industry Average"})[0] == "industry"
    # No type and no facility evidence stays unknown. California rejects
    # anything that is not facility-specific, so unknown must not qualify.
    assert b.classify_scope({"epd_type_stated": None})[0] == "unknown"
    assert b.classify_scope({})[0] == "unknown"
    # A single named plant is facility-level even with no type field.
    assert b.classify_scope(
        {"epd_type_stated": None,
         "facility_evidence": "manufactured at its facility located in North Salt Lake, Utah"}
    )[0] == "facility"


def test_classify_developer():
    record = {"preparer": {"email": "sustainability@sherwin.com", "name": "April Morris",
                           "org": ""}, "lca_software": None}
    assert b.classify_developer(record, "sherwin.com")[0] == "in_house"
    # Same person, but on a different company's domain.
    record = {"preparer": {"email": "david.green@mbcc-group.com", "name": "David Green",
                           "org": "Master Builders Solutions US LLC"}, "lca_software": None}
    assert b.classify_developer(record, "legacyprecast.com")[0] == "third_party_author"
    # A known consultancy, named only in the text.
    record = {"preparer": {"email": "", "name": "", "org": "WAP Sustainability Consulting"},
              "lca_software": None}
    assert b.classify_developer(record, "haworth.com")[0] == "consultant"
    record = {"preparer": {"email": "vrybl@sphera.com", "name": "Sphera", "org": "Vicki Rybl"},
              "lca_software": None}
    assert b.classify_developer(record, "vtindustries.com")[0] == "consultant"
    # Nothing found at all.
    assert b.classify_developer({"preparer": None, "lca_software": None}, "x.com")[0] == "unknown"


def test_score_company_is_per_manufacturer():
    """Five EPDs for one company is +2 once, not +2 each.

    Coatings is used because no state Buy Clean programme covers it, so
    the only rules that can fire are the count and the in-house rule.
    """
    records = [{
        "scope": "product", "material": "coatings", "developer": "in_house",
    } for _ in range(5)]
    total, reasons, facts = b.score_company(records)
    assert total == 4, reasons  # +2 five-or-more, +2 in-house
    assert any("+2 5 EPDs expiring" in r for r in reasons)
    assert sum(1 for r in reasons if r.startswith("+2 5")) == 1


def test_score_company_penalties():
    records = [{"scope": "product", "material": "coatings", "developer": "consultant"}]
    assert b.score_company(records)[0] == -2
    records = [{"scope": "product", "material": "coatings", "developer": "third_party_author"}]
    assert b.score_company(records)[0] == -2


def test_industry_wide_is_excluded():
    records = [{"scope": "industry", "material": "concrete", "developer": "in_house"}]
    total, reasons, _ = b.score_company(records)
    assert total == 0
    assert "excluded" in reasons[0]


def test_california_bonus_needs_facility_scope():
    """+3 fires only for a facility-specific EPD on a CA material.

    Mineral wool board is the Buy Clean Act's insulation product, and it
    also draws +2 for Minnesota, which is a tier 2 material there, so the
    totals include that. Plain insulation is Minnesota only.
    """
    records = [{"scope": "product", "material": "insulation", "developer": "in_house"}]
    assert b.score_company(records)[0] == 4  # +2 Minnesota, +2 in-house
    records = [{"scope": "facility", "material": "insulation", "developer": "in_house"}]
    assert b.score_company(records)[0] == 4  # no CA: not mineral wool board
    records = [{"scope": "product", "material": "mineral wool", "developer": "in_house"}]
    assert b.score_company(records)[0] == 4  # +2 Minnesota, +2 in-house
    records = [{"scope": "facility", "material": "mineral wool", "developer": "in_house"}]
    assert b.score_company(records)[0] == 7  # +3 CA, +2 Minnesota, +2 in-house


def test_domain_helpers():
    assert b.domain_of("https://www.Siplast.com/path") == "siplast.com"
    assert b.domain_of("david.green@mbcc-group.com") == "mbcc-group.com"
    assert b.domain_of(None) == ""
    assert b.is_third_party("afavilla@nsf.org")
    assert b.is_third_party("jgeibig@ecoform.com")
    assert not b.is_third_party("sustainability@sherwin.com")
    assert b.is_consultant("jgeibig@ecoform.com")
    assert not b.is_consultant("sustainability@sherwin.com")


def test_shift_months():
    assert b.shift_months("2027-06-03", 12) == "2026-06-03"
    assert b.shift_months("2026-11-01", 12) == "2025-11-01"
    assert b.shift_months("2027-03-31", 1) == "2027-02-28"


def test_timing_for():
    assert b.timing_for(-5) == "expired"
    assert b.timing_for(100) == "overdue - contact now"
    assert b.timing_for(300) == "contact now"
    assert b.timing_for(400) == "later"
    assert b.timing_for(None) == "unknown"


def test_us_address():
    assert b.is_us_address("1110 Harding Street Janesville, Wisconsin, 53545 United States")
    assert not b.is_us_address("SE Zone main building. Km 114 Kattemeya, Ain El Sokhna Egypt")


def test_insulation_is_not_automatically_california():
    """The Buy Clean Act's insulation product is mineral wool board.

    NSF's insulation categories are thermal, spray foam, continuous wall
    and roof insulation, so scoring them as California Buy Clean would
    pitch a roof-insulation renewal as a Buy Clean problem.
    """
    assert not b.MATERIALS["insulation"]["ca"]
    assert b.MATERIALS["mineral wool"]["ca"]
    assert b.CATEGORY_MATERIAL["roof insulation"] == "insulation"
    assert b.CATEGORY_MATERIAL["mineral wool board"] == "mineral wool"
    # The other states still cover insulation, so it keeps its +2.
    assert "MN" in b.MATERIALS["insulation"]["states"]


def test_declaration_family_lookup():
    """UL revises declarations, so the lookup is on the family.

    The Caltrans list carries 4790434237.101.1 for Vest and the document
    is 4790434237.101.3. A lookup on the whole number would miss it and
    leave the developer unknown, which is the thing the lookup exists to
    fix.
    """
    assert caltrans_aml._family("4790434237.101.1") == "4790434237.101"
    assert caltrans_aml._family("4790434237.101.3") == "4790434237.101"
    known = caltrans_aml.declarations()
    assert "4790434237.101" in known
    assert known["4790434237.101"]["preparer_org"] == "Sphera Solutions Inc"


def test_author_is_own():
    """EPD Hub names the author; only the manufacturer's own person counts.

    "on behalf of" is how an outside preparer signs, and the name in the
    author line is not the company, so WholeTrees' declaration is not
    in-house.
    """
    assert b._author_is_own("Prabhu Megharaj", "LuxWall Inc.", "LuxWall Inc.")
    assert not b._author_is_own("Travis Dahl, Dahl Management Inc.", "",
                                "REGEN Fiber")
    assert not b._author_is_own("Aurimas Bukauskas on behalf of WholeTrees.", "",
                                "WholeTrees Structures")


AML_HTML = """
<table>
<tr><td>Material Category: carbon steel rebar</td></tr>
<tr><td>Nucor Steel, Incorporated - Auburn</td><td>1 Steel Road, Auburn AL</td>
    <td>4790112345.101.1</td><td>1/1/2022 - 1/1/2027</td></tr>
<tr><td>Nucor-Yamato Steel - Arkansas</td><td>1 Yato Road, Blytheville AR</td>
    <td>4790112346.101.1</td><td>1/1/2022 - 1/1/2027</td></tr>
<tr class="expiredclass"><td>Gerdau Long Mill</td><td>1 Mill Rd, Long Beach CA</td>
    <td>4790112347.101.1</td><td>1/1/2020 - 1/1/2025</td></tr>
</table>
"""


def test_caltrans_aml_rows():
    """The AML rows, the company split and the live filter.

    "Nucor-Yamato Steel" is a different company from "Nucor Steel", and
    splitting on any hyphen would merge them and lose a lead.
    """
    rows = caltrans_aml.parse_aml(AML_HTML)
    assert len(rows) == 3, rows
    assert caltrans_aml.canonical_company("Nucor Steel, Incorporated - Auburn") == \
        "Nucor Steel, Incorporated"
    assert caltrans_aml.canonical_company("Nucor-Yamato Steel - Arkansas") == \
        "Nucor-Yamato Steel"
    assert caltrans_aml.parent_company("Gerdau Long Mill") == "Gerdau Long Mill"
    # The expired row is not a renewal lead.
    import datetime
    live = caltrans_aml.live_rows(rows, datetime.date(2026, 1, 1),
                                  datetime.date(2027, 12, 31))
    assert len(live) == 2, live
    assert live[0]["material"] == "rebar"


def test_remote_input_is_neutralised():
    """Remote strings reach the filesystem and the spreadsheet.

    The EPD number from a registry becomes a filename, and a company name
    from a registry becomes a cell. Neither may point outside the cache
    directory or run as a formula.
    """
    import os
    assert b.safe_cache_name("HUB-0080") == "HUB-0080"
    assert b.safe_cache_name("..") is None
    assert b.safe_cache_name("") is None
    for name in ("../../etc/passwd", "a/b\c", "HUB 0080"):
        cleaned = b.safe_cache_name(name)
        assert cleaned is None or os.sep not in cleaned
    assert b.cell_safe("=cmd()") == "'=cmd()"
    assert b.cell_safe("+1") == "'+1"
    assert b.cell_safe("@x") == "'@x"
    assert b.cell_safe("Nucor") == "Nucor"
    assert b.cell_safe(5) == 5


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print("ok", test.__name__)
    print(f"{len(tests)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
