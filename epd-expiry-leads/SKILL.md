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

NSF is the base. The JSON API behind <https://listings.nsf.org> gives:

| What | Endpoint |
|---|---|
| Every EPD listing, with its validity period | `POST products/searchstandardproducts` |
| Company address, phone, website | `POST company/search/products` |
| The EPD document itself | `https://nsfpubliclistingsprodeu.blob.core.windows.net/prod/Sustain/ProdCert/<EPD>.pdf` |

Both endpoints take the same empty search body, which is in
`scripts/build_lead_list.py` as `empty_search()`. The API needs no key.

Do not use `info.nsf.org`. That host is unreachable from every network we
tried, and the EPD PDFs it served now live in the blob storage above.

**NSF's published terms need written permission for this use.** They
prohibit "any form of data extraction or data mining, or other commercial
exploitation of any kind" without prior written permission. The lead list
is not republished as a dataset and uses no NSF mark, but it is a
commercial list built from extraction. Ask info@nsf.org. Until that
answer comes back, keep the request volume small and keep the script's
identifying User-Agent truthful. The full reasoning is in
`references/sources.md`.

**The Caltrans Authorized Materials List is where the California Buy
Clean materials live.** <https://mets.dot.ca.gov/aml/EnvironmentalProductDeclarations.php>
is a plain HTML table of the EPDs the state accepts, with the company,
the facility address, the declaration number and the exact validity
range. Caltrans' conditions of use put the site's information in the
public domain, and there is no robots.txt. NSF has no rebar or
structural steel category at all, so without this list the Buy Clean
half of the rubric scores nothing.

**The Steel Tube Institute publishes the UL Environment EPDs for the HSS
producers**, at
<https://steeltubeinstitute.org/hollow-structural-sections/hss-facility-specific-epds/>.
UL's own site blocks scripted access, so this is where the author of
those declarations comes from - and for Maruichi Oregon Steel Tube,
Searing and Vest the author is Sphera, a consultant, which is worth
knowing before the call. `references/declarations.csv` records what has
been read; the code merges it into the Caltrans rows.

Two more registries are readable and are worth adding when the coverage
matters more than the source count:

- **EPD Hub** (`manage.epdhub.com`) - a separate operator from Building
  Transparency, so EC3's licence does not govern it. Serves its own JSON
  listing with exact expiry dates, and its PDFs carry the EPD author,
  which is what the rubric needs. Its terms are the most permissive of
  any registry here, though the licence grant runs to the customer
  rather than to a third-party reader, so ask before making it
  commercial.
- **EPD Portal** (`epdportal.org`) - a multi-source search with a
  documented free API and a read-only MCP endpoint, built openly for
  agents. But it shows validity as a **year**, not a date, and it indexes
  European operators, not the US ones. It is a second pass: a worklist
  for a human, not a scored source.

The rest are not scriptable, and the reasons are in
`references/sources.md`:

- **EC3 / openEPD** has the data we want, including validity end dates and
  a developer field, but the free tier forbids commercial use. Lead
  generation needs the Practitioner tier or written permission.
- **EPD International** forbids building a data product from it and its
  robots.txt disallows the data site. Manual lookups only.
- **UL, ASTM, SCS** block scripted access. Manual lookups only.
- **Smart EPD** sends its EPDs to EC3, and its terms ban scripted retrieval.

So the material gaps in this list are **flat glass and mineral wool board
insulation**, and crawling more of the public web will not close them:
those are US programme EPDs, and the US programme operators are the ones
that block scripted access or charge for a licence. Rebar and structural
steel are no longer a gap. See `references/sources.md` before promising
coverage of the other two.

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

## Finding the people

The registries name a company and sometimes a contact address. They do
not name the person who renews the declaration, so that comes from a
people search.

- **Apollo** has the deeper index for large manufacturers. The people
  search is free; the bulk match that returns the LinkedIn profile URL
  and the business email costs 2 lead credits per person, against a
  75-credit monthly plan.
- **Lusha** has the better index for the small mills, which is most of
  the Caltrans list. One credit per company search, one per resolved
  person, six per contact enrich. Some of its person lookups return a
  compliance error, so a person can come back with a name and a title
  but no profile URL.

LinkedIn is the first channel, so the profile URL is the thing to
collect. Where there is no URL, the name and title are enough for a
person to find the profile, and a search string is worth carrying in the
workbook rather than leaving the row blank.

Both providers are B2B data brokers, so the first message carries a
source notice and a right to object, and a request to stop is honoured
within 10 business days. See `references/outreach.md`.

**The EPD's own contact is often the better first approach.** A
declaration that names an address names the person who wrote it, and for
a company whose EPD was written by a consultant, that consultant is the
one who has to be called for the renewal to happen at all.

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
