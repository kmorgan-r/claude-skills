"""Checks for the EPD expiry lead list builder.

Run with: python tests/test_build_lead_list.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import build_lead_list as b


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

    Insulation also draws +2 for Minnesota, which is a tier 2 material
    there, so the totals include that.
    """
    records = [{"scope": "product", "material": "insulation", "developer": "in_house"}]
    assert b.score_company(records)[0] == 4  # +2 Minnesota, +2 in-house
    records = [{"scope": "facility", "material": "insulation", "developer": "in_house"}]
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


def test_us_address():
    assert b.is_us_address("1110 Harding Street Janesville, Wisconsin, 53545 United States")
    assert not b.is_us_address("SE Zone main building. Km 114 Kattemeya, Ain El Sokhna Egypt")


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
