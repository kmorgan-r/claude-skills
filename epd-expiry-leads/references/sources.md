# Sources

What can be read programmatically, what cannot, and why. The distinction
matters because it decides whether a gap in the lead list is a bug or a
licensing boundary.

## Readable: NSF

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

## Not readable: EPD International / Environdec

<https://www.environdec.com>. The terms forbid building your own data
products from the data, and the data site's robots.txt disallows
automated access. Manual lookups only, and even then the results cannot
be republished as a dataset.

## Not readable: UL, ASTM, SCS

All three block scripted access. Manual lookups only.

UL is worth a special mention because it operates the Part A and Part B
PCRs that the NSF EPDs reference, and a lot of the missing materials sit
in UL's programme.

## Not readable: Smart EPD

<https://smartepd.com>. Its library has an expiration filter, but the
filter sends you to EC3, so the data is EC3's data and the licence
problem comes with it. Its terms of use explicitly ban scripted
retrieval.

## Not readable: EPD Portal

The EU's portal. It carries almost no US programme operators, so it is
not a source of US Buy Clean leads.

## What this means for coverage

The lead list is complete for what NSF lists. It is not complete for
California Buy Clean materials, because the materials NSF does not list
are the ones in the registries we cannot read.

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
not in a licensed export.
