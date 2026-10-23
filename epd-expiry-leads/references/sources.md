# Sources

What can be read programmatically, what cannot, and why. The distinction
matters because it decides whether a gap in the lead list is a bug or a
licensing boundary.

Two things changed on 2026-10-23. The NSF caveat below matters more than
it looks. And three further registries turned out to be readable, which
means the "not readable" list is shorter than it was.

| Registry | Readable | Expiry date | US coverage | Terms |
|---|---|---|---|---|
| NSF | yes, JSON API | exact date | the base of the list | needs written permission, see below |
| EPD Hub | yes, JSON + PDFs | exact date | 101 EPDs, 6 in window | most permissive of the lot |
| EPD Portal | yes, documented API + MCP | **year only** | 77 EPDs | source terms govern |
| EPD International | HTML + API, list embedded | on the PDF | large | forbids data products |
| EC3 | paid tier only | exact date | large | paid tier required |
| UL, ASTM, SCS | no - they block scripted access | | | |
| Smart EPD | no - scripted retrieval banned, data is EC3's | | | |

**None of them closes the California Buy Clean gap.** The missing
materials - structural steel, rebar, flat glass - are US programme EPDs
living in NSF, UL, ASTM and SCS, and of those only NSF is scriptable. The
European registries have plenty of rebar and steel, but they are European
EPDs for European projects, not the US EPDs a California buyer needs.

What the extra sources buy is coverage of US manufacturers who publish
through a European programme operator. That is a real, if small, addition:
6 more US companies in the expiry window from EPD Hub, 12 more US EPDs
expiring in 2027 from EPD Portal.

## Readable: NSF (with a licensing caveat - read this)

The JSON API behind <https://listings.nsf.org>.

- `POST products/searchstandardproducts` with the empty search body in
  `empty_search()`. Returns every EPD listing with the company name,
  product, NSF product category, PCR code and the certification period.
- `POST company/search/products` with the same body. Returns the company
  address, phone and, for some registrants, a website. `companyURL` is
  null for many.
- EPD documents at
  `https://nsfpubliclistingsprodeu.blob.core.windows.net/prod/Sustain/ProdCert/<EPD>.pdf`

The certification period is the field the whole exercise turns on:
`06/24/2022 - 06/24/2027` is US month-first, year-last. It is the only
field in the API that needs parsing rather than reading, and getting it
backwards silently selects the wrong rows.

`info.nsf.org` is the old host and it is unreachable. Curl, WebFetch,
headless Chromium and four proxy routes all failed, and the Wayback
Machine has no snapshot of the listing pages. The API replaced it and
works.

### The caveat: NSF's published terms are narrower than "it's a public API"

<https://www.nsf.org/privacy-copyright> is the operative terms document
for the listings (there is no separate API or bulk-data licence published).
Two clauses matter:

- "These materials may not be copied for **commercial use or
  distribution**, nor may these materials be modified or **reposted to
  other sites**, without the prior express written permission of NSF
  International."
- "Any use of materials on this site, including reproduction... any form of
  **data extraction or data mining, or other commercial exploitation of any
  kind**, without prior written permission of NSF is strictly prohibited."

The second clause covers reading the listing and building a lead list
from it. This is not a grey area the way a robots.txt is: the terms name
"data extraction" and "commercial exploitation" explicitly.

**How this was read, and what it does and does not permit.** The lead list
identifies companies to contact about renewing their own EPD. It is not
republished as a dataset, it is not a substitute for the listings site,
and it uses no NSF mark. But it is a commercial list built from data
extraction, so on a literal reading of the clause it needs NSF's written
permission.

**Get the permission.** One email to info@nsf.org, or data.protection@nsf.org
for anything touching the data itself, asking for written permission to
use the certified-products listings for lead generation. Cost: an email.
Benefit: the whole lead list stops being a compliance question, and the
answer is likely to be a yes, because EPDs are published precisely so
that people can find them.

Two things that reduce the exposure in the meantime, neither of which is
a substitute for permission:

- The script identifies itself honestly. `UA = "Mozilla/5.0 (compatible;
  epd-expiry-leads/1.0)"`. Do not change this to a browser string to hide
  the traffic. If NSF asks who is calling, a truthful answer is a much
  better position than a disguised one, and the identifying UA is also
  what makes the permission request credible.
- The volume is small. A full run is 2 listing API POSTs plus one GET
  per EPD, about 119 requests. That is a person clicking through the
  listings site, not a crawler. The PDF cache means a re-run costs 2
  requests. Keep it that way - do not add a multi-registry crawler, and
  do not run it in a loop.

### What the API does not give

- **Flat glass, rebar, structural steel.** NSF has no product category
  for any of them. Its only California Buy Clean material is thermal
  insulation, plus steel brackets.
- **EPD type and scope.** The API carries neither. Both are read from
  the PDF, and some templates omit the EPD type field entirely.
- **The preparer.** Also PDF only.
- **A website for many registrants.** `companyURL` is null often enough
  that the domain usually comes from the PDF or from
  `company-domains.csv`.
- **Some PDFs.** A handful of listings point at a document that is not
  in blob storage. The listing stays, the document is missing.

## Readable: EPD Hub

<https://manage.epdhub.com> is the public library of **EPD Hub BV**
(Amsterdam, company no. 869165069), an ISO 14025 programme operator and an
ECO Platform member. It is a different organisation from Building
Transparency, so EC3's licence does not govern it.

The list is served by the site's own JSON endpoint:

```
GET https://manage.epdhub.com/?request_type=ajax&page=1&page_size=100
```

It takes `page`, `page_size`, `sort`, `search`, and the filters `category`,
`product_type`, `manufacturer`, `country`, `date_published` and
`date_valid`. **Date filters are ISO `YYYY-MM-DD`** and `date_valid` is
passed as a comma-separated range: `date_valid=2026-11-01,2027-10-31`.

Each record carries `epd_number`, `manufacturer`, `product_name`,
`product_type`, `category`, `country`, `date_published`, `date_valid` and
an `epds` array with a PDF download path. Fetch the PDF from
`https://manage.epdhub.com` + that path.

The PDFs are EN 15804 documents and they carry everything the lead list
needs: manufacturer address, contact email, website, **EPD author**
(the field that decides the in-house vs consultant score), verifier,
scope, and "valid until <date>" in the header text.

Scale: 7,475 EPDs, 101 of them US. In the 2026-11 to 2027-10 window, 364
EPDs expire and 6 of those are US.

### Why it is the most usable registry here

<https://www.epdhub.com/general-terms-and-conditions> is a two-page PDF,
"24 March 2026", governing "all use of services, documents and data
provided by EPD Hub B.V.". What it says:

- "The Operator grants the Customer a non-exclusive, limited license to
  use the final, verified EPD documents. The use is limited to the
  duration of the EPD validity and for use for which EPDs are appropriate."
- Section 2 confirms that "the names of company representatives, contact
  persons, and independent verifiers" are **made public on the EPD
  document** and that the EPD is "available for public download" once
  published. The legal basis is performance of contract, and the data is
  retained for as long as the EPD remains valid.
- No clause prohibits scraping, automated retrieval, bots, building a
  database, commercial use, or redistribution. There is no "data
  extraction or commercial exploitation" language of the kind NSF and
  EC3 have.
- There is no robots.txt on manage.epdhub.com (404). That is not a
  permission, but it is not a prohibition either.

Two honest caveats. The endpoint is the site's own internal AJAX, not a
published API, so it can change without notice - treat it as
unstable and keep the parser tolerant. And the licence grant in section 1
runs to the **Customer** (the manufacturer who published the EPD), not
expressly to a third party reading the library, so the terms do not
clearly authorise commercial use of the library data by an outsider. A
short email to contact-us@epdhub.com asking whether a consultancy may
use the library for commercial lead generation would close that gap for
the price of one message. Ask before building on it.

## Readable: EPD Portal

<https://epdportal.org> is a **multi-source search** operated by
Multicert (Poland), which also runs the EPD Polska programme. It indexes
public EPD metadata from 11 operators: EPD-Global / EPD Norge (12,044),
IBU (5,825), ÖKOBAUDAT (5,518), EPD Italy (2,874), EPD Danmark (1,647),
Kiwa-Ecobility Experts (700), AENOR GlobalEPD (492), ITB (470), EPD
Ireland (366), RTS EPD (265), EPD Poland (59). 30,260 records in total.

This is the friendliest source to build on, because it documents itself
for exactly this use. <https://epdportal.org/for-agents/> gives a
read-only MCP server and a REST API, both free and anonymous:

```
GET https://data.epdportal.org/api/facets
GET https://data.epdportal.org/api/search?q=concrete&sort=recent&limit=25&offset=0
```

The OpenAPI description is at
`https://epdportal.org/wp-json/epdportal/v1/openapi` (server
`https://data.epdportal.org/api`). The MCP endpoint is
`https://epdportal.org/wp-json/epdportal/v1/mcp`, which exposes
`search_epds` and `list_epd_sources`. Note the MCP endpoint rejects GET
with 405 - it wants a POST with a JSON-RPC body, so drive the REST API
unless you are wiring it into an MCP client.

The critical limitation, stated by the operator: **"Validity is shown as
a year, not an exact expiry date."** A record carries `valid_until: 2031`
where NSF gives a day. A lead list that ranks by days-to-expiry needs the
exact date, so this source can only ever be a second pass that flags
"expires in 2027, go and get the exact date at source".

US coverage is 77 records, all from EPD-Norway, IBU and ÖKOBAUDAT - the
European operators, publishing for US manufacturers. 12 of the 77 expire
in 2027, 11 in 2028, 43 in 2031.

Its terms are also the most explicitly written down: "Full external
datasets are not republished by this API. Source terms govern reuse, and
source presence does not imply a partnership." So EPD Portal being free
and open does not grant any right over EPD-Norway's data. Read the
source operator's terms, not EPD Portal's.

## Readable with effort: EPD International / Environdec

<https://www.environdec.com/library> is a Next.js app. The API host is
`https://api.prod.environdec.com`, and the endpoints visible in the app's
JS chunk are `/api/v1/EPDLibrary/Collections/`, `/api/v1/EPDLibrary/EPD/`,
`/api/v1/EPDLibrary/Files/EPDs/`, and
`/api/v2/EPDLibrary/Files/<id>/Data` for a document. The list endpoint
did not resolve - the record set appears to be embedded in the page's
own flight data, and the page is 493 KB of it.

The blocker is the terms, not the access. <https://www.environdec.com/general-terms>
says users may not "redistribute or sell the EPD data or any parts
thereof", may not "use them for the creation of another data product",
and may not "create and/or distribute data products of their own".
Attribution to the International EPD System and its trade mark is
required. Their robots.txt disallows `/library` for one named bot and
`/search` for everyone.

So: readable, and not usable for a commercial lead list without their
written permission.

## Not readable on the free tier: EC3 / openEPD

<https://buildingtransparency.org/ec3> has the best structured data of any
registry. Each EPD carries a validity end date and a developer field, and
the API filters on validity end date, which is exactly what a lead list
needs.

The blocker is the licence tier, not the access, and the licence is
commercially obtainable. A paid tier is the sanctioned way to do this
kind of work - nothing about using EC3 for lead generation violates their
terms once you are on it. The free tier is the thing that forbids it.

Current terms, from <https://www.buildingtransparency.org/api-terms-of-service/>
and <https://www.buildingtransparency.org/api-access-pricing/>:

| Tier | Annual dues | What it grants |
|---|---|---|
| Free | $0 | "research, learning, and pilot integrations" only. No right to "store, cache for reuse, or deploy EC3 data in commercial or production applications." |
| EC3 Practitioner Member | $5,000 | "production and commercial use", plus the right to access, store, cache and reuse EC3 API Data during the term |
| EC3 Sustaining Member | $10,000-$25,000 | Same rights, highest rate limits |

Lead generation is commercial use, so it needs a paid tier. $5,000 a year
is the entry price. There are three cheaper routes worth trying first:

1. **Write to support@buildingtransparency.org and ask.** The API Terms
   require notification before integrating EC3 data into a platform or
   database anyway, so the conversation has to happen regardless. Ask
   about the Practitioner tier at the same time.
2. **Ask for a waiver.** "Discretionary, time-limited waivers that provide
   increased API access at no cost" are available for research, education
   and mission-aligned work, granted for up to 12 months and renewable.
   Whether a lead list for a climate consultancy counts is their call,
   not ours.
3. **Ask for the discount.** Government agencies, US 501(c)(3) bodies and
   non-US nonprofits get 50% off dues. Not obviously applicable, but it
   costs one email.

### What the paid tier still does not let us do

Buying the licence does not buy everything. Three limits survive the
payment, and they are the ones to design around:

- **"Resale, sublicensing, or redistribution of EC3 data as a standalone
  data product remains prohibited for all tiers"** without express written
  consent. So the lead list itself cannot be published, sold, or handed to
  a client as a dataset. Using EC3 data to run our own sales outreach is
  commercial use of the data; shipping the data is redistribution of it.
  That distinction matters if anyone ever asks for the list as a product.
- **Notification is required before integration**, not just before
  resale: "users must notify BuildingTransparency of their intent to
  integrate the data into their platforms or databases" before
  commencing. Continuing use under an existing agreement needs notice
  again. Notification goes to support@buildingtransparency.org. Failing
  to notify means immediate suspension, 14 days to cure, then permanent
  termination.
- **Attribution is required** and cannot be removed or obscured. Direct
  links to the original EC3 records when presenting EC3 data, and an
  acknowledgement of Building Transparency and EC3 in customer
  communications. Using the Building Transparency name or logo in
  public-facing material needs separate notice. This collides with the
  rule that ClimatePoint is not affiliated with any programme operator, so
  the safest read is: attribute the data, never imply affiliation, and
  leave the logo alone unless they grant it.

The paid licence is also revocable and runs only "solely during the
Term", annual by default. A lead list built on it has to be rebuilt each
year rather than treated as a one-off harvest, and two-year prepayment is
10% off if the coverage is worth committing to.

Rate limits differ between the pricing page and the developer docs, and
the pricing page still carries a placeholder where the member limits
should be. Confirm the current numbers with them rather than relying on
either page.

This is the registry most likely to close the flat glass and rebar gap.
Worth a decision on the licence, not worth working around.

## Not readable: UL, ASTM, SCS

All three block scripted access. Manual lookups only.

UL is worth a special mention because it operates the Part A and Part B
PCRs that the NSF EPDs reference, and a lot of the missing materials sit
in UL's programme. UL's EPDs live at <https://spot.ul.com/>, which
answers 405 to every request including robots.txt. <https://epdportal.org>
does not index UL.

ASTM's robots.txt is permissive (`Allow: /` with two paths disallowed) and
its site is reachable, but no EPD listing endpoint was found.
SCS's robots.txt disallows four paths and nothing else; the site loads.
Neither yielded a listing API.

## Not readable: Smart EPD

<https://smartepd.com>. Its library has an expiration filter, but the
filter sends you to EC3, so the data is EC3's data and the licence
problem comes with it. Its terms of use explicitly ban scripted
retrieval. Its robots.txt is permissive but its terms are not.

## What this means for coverage

The lead list is complete for what NSF lists, plus 6 more US companies
from EPD Hub and 12 US EPDs flagged for 2027 from EPD Portal. It is still
not complete for California Buy Clean materials, and no crawl of the
public web will change that: **the missing materials are US programme
EPDs, and the US programme operators are the ones that block scripted
access or charge for a licence.**

Crawling more sites does not close this gap. It adds European EPDs for
European projects, which is not what a California buyer needs.

Two ways to close it, in order of effort:

1. **Get onto EC3.** Ask support@buildingtransparency.org for a waiver
   first, since it is free and might apply. Failing that, the EC3
   Practitioner tier at $5,000 a year buys full structured coverage with
   validity dates and developer fields, and an API that would make this
   script better rather than longer. Notification before integration is
   required either way.
2. **Use a headless browser on the manual-lookup registries.** The
   terms permit a person reading a page, so a browser session that
   behaves like a person is defensible where a scraper is not. This is
   slower and more fragile than the API, and the output cannot be
   republished.

The headless browser route is also the only way to see EPD type,
facility-specificity and developer fields for the non-NSF registries
without the EC3 API, since those registries expose them on the page but
not in a licensed export. Note that EPD Hub's PDFs already give the EPD
author and scope, so a browser is only needed for UL, ASTM and SCS.

### And ask NSF

Separately from EC3, NSF's own terms need written permission for what
this skill already does. See the caveat at the top of this file. That
email should go out regardless of which other source is used.
