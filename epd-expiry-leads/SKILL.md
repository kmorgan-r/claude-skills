---
name: epd-expiry-leads
description: Use when the user wants an EPD expiry lead list, needs to find manufacturers whose Environmental Product Declarations are about to expire, wants to rank EPD renewals as sales leads, or asks to find contact details for companies with expiring EPDs. Reads the NSF public listings API and the EPD PDFs, applies the Buy Clean ranking rubric, and exports a spreadsheet. Ends by handing the top company domains to the find-cold-leads skill.
---

# EPD expiry lead list

Finds manufacturers whose EPDs are about to expire, ranks them with the Buy
Clean rubric, and exports a spreadsheet with a contact for each.

An expiring EPD is a deadline a manufacturer has to meet, which makes it a
reason to talk to them. The stronger the reason, the better the lead: an
EPD that expired last month and a state Buy Clean programme that rejects
your customer's bid are better than a routine renewal.

## Sources

NSF is the only EPD registry that can be read programmatically without a
licence. The JSON API behind <https://listings.nsf.org> is the source:

| What | Endpoint |
|---|---|
| Every EPD listing, with its validity period | `POST products/searchstandardproducts` |
| Company address, phone, website | `POST company/search/products` |
| The EPD document itself | `https://nsfpubliclistingsprodeu.blob.core.windows.net/prod/Sustain/ProdCert/<EPD>.pdf` |

Both endpoints take the same empty search body, which is in
`scripts/build_lead_list.py` as `empty_search()`. The API needs no key.

Do not use `info.nsf.org`. That host is unreachable from every network we
tried, and the EPD PDFs it served now live in the blob storage above.

Other registries are not scriptable, and the reasons are in
`references/sources.md`:

- **EC3 / openEPD** has the data we want, including validity end dates and
  a developer field, but the free tier forbids commercial use. Lead
  generation needs the Practitioner tier or written permission.
- **EPD International** forbids building a data product from it and its
  robots.txt disallows the data site. Manual lookups only.
- **UL, ASTM, SCS** block scripted access. Manual lookups only.
- **Smart EPD** sends its EPDs to EC3, and its terms ban scripted retrieval.

So the material gaps in this list, flat glass and rebar, are real and they
are in the non-NSF registries. See `references/sources.md` before promising
coverage of them.

## Running it

```bash
python scripts/build_lead_list.py \
  --out reports/epd-expiry-leads.xlsx \
  --csv reports/epd-expiry-leads.csv \
  --handoff reports/find-cold-leads-handoff.csv
```

Defaults to the window in the original request, 2026-11-01 to 2027-10-31.
Useful flags:

| Flag | Effect |
|---|---|
| `--start` / `--end` | Change the expiry window |
| `--refresh` | Re-download the cached PDFs |
| `--no-pdfs` | Listing data only, no PDF parsing. Fast, no contacts |
| `--cache-dir` | Where the parsed PDFs are cached (default `data/pdf-cache`) |
| `--overlay` | A CSV of manually verified company domains |
| `--handoff` | Write a company-domain CSV for `find-cold-leads` |
| `--handoff-limit` | How many companies in the handoff file (default 50) |

The PDF cache means a second run is fast. Use `--refresh` when the parsing
changes, or when a listing points at a new document.

Output is a workbook with three sheets:

- **Leads** - one row per manufacturer, ranked by score
- **EPDs** - one row per EPD, with the parsed fields and the source PDF URL
- **California watch** - every California Buy Clean material EPD in the
  registry with its expiry date, so the gap below is visible

The handoff file is the input to `find-cold-leads`: company, domain, score,
timing, expiry, the contact the PDF named, the material and the reason for
the score. US companies only, because CASL applies to the rest.

## The rubric

Applied once per manufacturer, not once per EPD. The full rubric and the
evidence behind each rule are in `references/ranking-rubric.md`.

| Rule | Points |
|---|---|
| California Buy Clean material with a facility-specific EPD | +3 |
| Material covered by another state's Buy Clean programme | +2 |
| Five or more EPDs expiring | +2 |
| Declaration prepared in-house, so there is an LCA team to pitch | +2 |
| Developed by a known consultant | -2 |
| Written by a third party that also supplies the manufacturer | -2 |
| Developed by an automated tool | -2 |
| Industry-wide EPDs | excluded |

The score is a ranking aid, not a filter. Read the `reasons` column before
acting on a row.

## Reading the PDF

The EPD document is where the lead lives. The listing gives a name and an
expiry date; the PDF gives the declaration holder, who prepared it, the
verifier, the PCR reviewer and a contact.

Field names differ between the NSF templates, so `parse_pdf` handles each
shape it has met: `LCA Preparer`, `Declaration Prepared by`, `Author of
the Life Cycle Assessment`, `LCA CONSULTANT` and the `... was conducted in
... by:` form. When a template is new, add a pattern rather than loosening
the existing ones, because a loose pattern starts matching the verifier
line and reports a consultant as the author.

Two things to keep straight when reading a PDF:

- **The verifier is not the author.** Every EPD has a verifier, usually
  EcoForm or NSF, and the PCR reviewer is usually Industrial Ecology
  Consultants. Neither says who wrote the declaration.
- **The author is often not the manufacturer.** Master Builders Solutions
  writes the concrete EPDs for several registrants in this list while
  selling them admixtures. An EPD it wrote is not an in-house
  declaration, and the renewal will not run through the manufacturer.

`domain` and `domain_source` say where each domain came from. Read
`domain_source` before trusting it. NSF leaves `companyURL` null for many
registrants, so the domain is often taken from the PDF or from
`references/company-domains.csv`, which records a verified domain and the
URL it came from.

## Handing off to find-cold-leads

The `--handoff` flag writes the company domains and the PDF contact into
one CSV. The PDF contact is often better than anything Apollo returns, so
it travels with the domain and Apollo adds named people on top.

Then run the `find-cold-leads` skill with those domains and the theme
"environmental product declaration renewal" or similar. It needs the
Apollo MCP, which is not connected by default. See
`references/apollo-handoff.md` for how to get Apollo working and for the
two pipeline settings that matter here.

Do not skip the `us_based` column. Ecobat's registrant is in Egypt and
Teknion's in Canada, and they are in the list because NSF lists them, not
because they are US Buy Clean prospects. CASL applies to the Canadian
contact, not just CAN-SPAM. The handoff file excludes them.

## Outreach

`references/outreach.md` covers the timing, the CAN-SPAM and CASL
obligations, and the non-affiliation wording. Read it before sending
anything. In short: California requires the EPD to be valid at
installation, so an EPD expiring in nine months can already fail bids
being made today, and first contact belongs 9 to 12 months before expiry.

Three columns carry the timing:

- `days_to_expiry` - days from today to the earliest expiry
- `contact_by` - twelve months before that, the date the window opens
- `timing` - `overdue - contact now` inside nine months, `contact now`
  between nine and twelve, `later` beyond

In the 2026-11 to 2027-10 window nearly every row reads `overdue` or
`contact now`, because the window starts a month from now. The `timing`
column becomes useful on a later window, and the `days_to_expiry` column
sorts the near ones.

## Checks

```bash
python tests/test_build_lead_list.py
```

Covers the date parser, the scope classifier, the developer classifier and
the rubric. The date parser has its own test because getting month and day
the wrong way round silently filters the wrong rows and every number
downstream looks plausible while being wrong.
