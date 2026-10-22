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

## Not readable: EC3 / openEPD

<https://buildingtransparency.org/ec3> has the best structured data of any
registry. Each EPD carries a validity end date and a developer field, and
the API filters on validity end date, which is exactly what a lead list
needs.

The blocker is the licence, not the access. The free tier forbids
commercial use. Lead generation is commercial. It needs the Practitioner
tier at about $5,000 a year, or written permission from
support@buildingtransparency.org.

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

1. **Buy the EC3 Practitioner tier.** Full structured coverage with
   validity dates and developer fields, and an API that would make this
   script better rather than longer.
2. **Use a headless browser on the manual-lookup registries.** The
   terms permit a person reading a page, so a browser session that
   behaves like a person is defensible where a scraper is not. This is
   slower and more fragile than the API, and the output cannot be
   republished.

The headless browser route is also the only way to see EPD type,
facility-specificity and developer fields for the non-NSF registries
without the EC3 API, since those registries expose them on the page but
not in a licensed export.
