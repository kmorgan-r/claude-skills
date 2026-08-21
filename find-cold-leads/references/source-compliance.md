# Source & Compliance Boundaries (region-aware)

This skill organizes lead research and bakes in a **compliance wall**. It does not
decide that any specific outreach is legal — it prepares **review-gated leads** and
records the basis so a human can decide. *Engineering guidance, not legal advice.*

## The wall: having an email ≠ permission to send it

Two separate laws stack on a cold email:

1. **ePrivacy / national law governs *sending*** the marketing email.
2. **GDPR governs *holding/processing*** the personal data (the email address).

So even a verified Apollo email is, for an EU contact, a *lead for review*, not a
send-ready address.

## Region is set at SEARCH time

Tag `region` from the Phase 1 Apollo search location filter
(`person_locations` / `organization_locations` on `apollo_mixed_people_api_search`)
— **not** from the enrich-only `country` returned by `apollo_people_bulk_match`,
because most rows are never enriched. Enrich `country` only *refines* the
search-time region (override if it clearly contradicts). Unknown region →
conservative EU posture.

## Per-region posture

| Region | `consent_status` | `outreach_allowed_review` | Notes |
|---|---|---|---|
| **Germany** (UWG §7) | `unknown` | `needs review` | Strictest — express consent generally required **even B2B**. Flag `strict`. Prefer role-based path. |
| **EU (other) / UK** | `unknown` | `needs review` | France/UK professional addresses more permissive with opt-out + relevance; still document legitimate interest. |
| **US** | `n/a (opt-out)` | `ok with working unsubscribe + sender ID` | CAN-SPAM: named-email outreach defensible with unsubscribe + accurate identity. |
| **Unknown** | `unknown` | `needs review` | Conservative EU default. |

`compliance_fields()` in the legacy crawler script applied this; the Apollo
pipeline applies the same posture when it builds the Leads sheet.

## GDPR specifics for EU rows

- **Lawful basis** = legitimate interest (Art. 6(1)(f)) — relevance to the
  contact's professional role; document it in `legitimate_interest_basis`.
- **Art. 14 transparency** — the data came from a source other than the person
  (Apollo / public web), so a source notice + right to object is owed, typically in
  the first email or a linked privacy notice.
- **Right to object / erasure** must be honored; keep a suppression path.
- **Role-based addresses** (`sustainability@`, `info@`) are weakly/not personal
  data → lower risk; prefer them for strict regions.

## Apollo as the source

The pipeline's contact data comes from **Apollo** (licensed public professional
data), not from crawling the company's own pages. That changes the provenance
rule but not the compliance wall:

- Record `source_provider` (`apollo_mixed_people_api_search +
  apollo_people_bulk_match`) and `source_basis` (`Apollo people DB; business
  email; public professional data`) on every lead.
- Apollo's `linkedin_url` is licensed data and fine to store as
  `linkedin_reference_url` — still do not scrape LinkedIn (see boundary below).
- Stage Q evidence (`evidence_snippet`, `source_url`,
  `business_relevance_basis`) is still recorded from the free Phase 1 search
  result (org industry + title + domain) so the qualify-or-reject call is
  auditable.
- The old ZoomInfo-snippet failure (a contact whose evidence was a third-party
  data-vendor snippet rather than the company's own pages) is not a concern
  here, because Stage Q qualifies on Apollo's structured org/title fields, not
  on a scraped snippet.

## LinkedIn boundary

- Allowed: store a LinkedIn URL as a reference (`linkedin_reference_url`); accept a
  user-provided/licensed export as a seed. Apollo's `linkedin_url` is licensed data
  and fine to store as a reference.
- Not allowed: automated LinkedIn browsing, logged-in scraping, harvesting
  connections/profiles, or treating LinkedIn as the contact-permission evidence.

## Honesty rule

State plainly in every run summary: **EU/DE rows are human-review-gated leads, not
a send-ready cold list.** Do not let "quality German leads" be read as
"cold-email-ready German leads."
