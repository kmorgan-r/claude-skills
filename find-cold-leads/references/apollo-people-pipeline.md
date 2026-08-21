# Apollo People Pipeline — real named contacts with verified business emails

This is the skill's primary workflow. It yields **real named people** (verified
business email + title + seniority + company firmographics), not company rows or
role-based catchalls. Proven on the DPP pilot: 164 domains → 77 people → 25
enriched → 25/25 verified emails for 25 credits.

The pipeline runs through the **Apollo MCP tools directly** (no script). Two
phases: a **free** people search, then a **paid** bulk enrichment gated by
Stage Q and a credit budget.

## Prerequisites

- Apollo MCP connected (OAuth — no API key stored in any file).
- A list of **company domains** to search (from prior account discovery, a seed
  file, or a user-provided list). The pipeline does not discover companies — it
  discovers people *at known companies*. Use a theme (see `search-themes.md`) to
  define which sectors count as ICP fit.
- Buyer titles from the theme's `contact_search_titles` (→ Apollo `person_titles`).
- Credit balance read at run start (`apollo_usage_stats_credit_usage_stats`).

## Phase 1 — free people search (0 credits)

Call `apollo_mixed_people_api_search`:

| Parameter | Value | Why |
|---|---|---|
| `q_organization_domains_list` | array of ~20 domains per call | batch domains; free; pages across batches |
| `person_titles` | theme `contact_search_titles` array | buyer-role filter |
| `include_similar_titles` | `true` (default) | catches title variants ("Sostenibilita", "CSR & Sustainability", etc.) |
| `per_page` | `100` | small `per_page` silently caps results at page 1 |
| `person_locations` / `organization_locations` | optional | narrow by region if theme specifies |

Page through (`page` + `per_page`) while `has_more`. 0 credits regardless of
result count.

**What you get per person:** `id`, `first_name`, `last_name` (often
masked/obfuscated in search results — expected, does not block enrichment),
`title`, `has_email` flag, `organization.name`. You do **not** get emails or
phones here — that is Phase 2.

Collect every person into a keep set. Record the batch each came from for the
Sources sheet.

## Rank and cap (free, before any spend)

1. **Seniority score** from title:
   - Chief / CSO / C-suite = 6
   - VP / Vice President / Head of = 5
   - Director = 4
   - Senior Manager = 3
   - Manager / Lead = 2
   - other = 1
2. Per company, sort: `has_email=true` first, then seniority desc.
3. **Cap 3 candidates per company** (avoid over-indexing one firm).
4. Global sort: `has_email` then seniority. The top N (≤ budget) with
   `has_email=true` are the enrichment candidates.

## Stage Q — qualify on free signals (never enrich to qualify)

Tier each candidate from the free search result (org industry + title + domain),
**before** spending a credit:

- **strong** — ICP sector (theme `sectors`) + buyer title (theme
  `buyer_title_terms`) + resolved official domain.
- **possible** — sector or title fit, one leg missing.
- **reject** — off-ICP (services, SaaS, consultancy, retailer, competitor selling
  LCA/EPD tooling), data vendor/directory, listicle title, title that is a SERP
  fragment rather than a real role.

Only `strong` / `possible` proceed to Phase 2. Record tier + evidence
(`evidence_snippet`, `source_url`, `business_relevance_basis`) so the call is
auditable. See `SKILL.md` Qualification (Stage Q).

## Phase 2 — paid bulk enrichment (1 credit/match, 0 no-match)

Call `apollo_people_bulk_match` on the top N enrichment candidates:

| Parameter | Value | Why |
|---|---|---|
| `details` | array, max **10 per request**; each = `{id, first_name, organization_name}` (`id` = Apollo person id from Phase 1) | `id` preferred; split N into batches of ≤10 |
| `reveal_personal_emails` | `false` | **business email only** |
| `run_waterfall_email` | `false` | no waterfall — credit cost variable/plan-dependent |
| `run_waterfall_phone` | `false` | no phone waterfall |
| `reveal_phone_number` | `false` | no phone reveal |

Stop when the run budget (default **25**) is exhausted. Keep remaining
`has_email=true` candidates company-level in a **Remaining Candidates** sheet for
a future budget cycle.

**What you get per matched person:** `email` (business), `email_status`
(`verified` / `likely to engage` / …), unmasked `first_name` / `last_name`,
`linkedin_url`, `organization` firmographics (`name`, `primary_domain`,
`country`, `industry`, `estimated_num_employees`, `organization_revenue_printed`),
`seniority`. A no-match costs 0 and returns nothing for that person.

### Credit accounting

- 1 credit per matched person, 0 per no-match.
- Record the spend per row so the Run Config total reconciles against a
  post-run `apollo_usage_stats_credit_usage_stats` delta.
- **Surface the `mcp_credits` block to the user** whenever Apollo returns one —
  estimated cost before a spend, actual spend + new balance after.

## Dedup vs Odoo (before export)

Query Odoo `mailing.contact` for the matched business emails (via the
climatepoint-odoo MCP `search_read`). Mark `odoo_dupcheck` per row. Do not upload
duplicates. Upload only after the user reviews and marks `odoo_ready=yes`.

## Export — Excel workbook

Sheets:

- **Leads** — one row per enriched person. Columns: `contact_name`,
  `first_name`, `last_name`, `job_title`, `business_email`, `email_status`,
  `linkedin_reference_url`, `company_name`, `company_domain`, `company_country`,
  `company_industry`, `company_employees`, `company_revenue`, `seniority`,
  `apollo_person_id`, `apollo_org_id`, `source_provider`, `source_basis`,
  `outreach_allowed_review`, `gdpr_legitimate_interest_basis`,
  `art14_source_notice`, `opt_out_provided`, `personal_email_used`,
  `waterfall_used`, `odoo_ready`, `odoo_dupcheck`.
- **Remaining Candidates** — `has_email=true` people not enriched (budget cap),
  company-level, for next cycle.
- **Sources** — Phase 1 free-search batches + Phase 2 enrichment batches, with
  credits and result counts.
- **Run Config** — run name, date, source domain list, ICP, buyer persona,
  search titles, domains searched, companies with a buyer lead, people found
  free, people enriched, emails verified, credits budget / spent / remaining,
  compliance basis.
- **Credit Ledger** — per-credit-type limit / consumed / remaining + run total.

Style: header fill `1F4E78`, white bold font, `freeze_panes = "A2"`.

## Compliance lock (non-negotiable)

- **Business email only** — `reveal_personal_emails=false`.
- **Waterfall off**, **no phone reveal** — `run_waterfall_email/phone=false`,
  `reveal_phone_number=false`.
- **LinkedIn URLs are reference only** — store `linkedin_url` as
  `linkedin_reference_url`; do not scrape LinkedIn.
- **`outreach_allowed_review` = `needs review`** until the user confirms basis.
- **GDPR legitimate interest** (Art. 6(1)(f)) — relevance to the contact's
  professional role; record in `gdpr_legitimate_interest_basis`.
- **Art. 14 source notice + opt-out** — data came from Apollo (not the person);
  include a source notice + right to object in outreach. Record in
  `art14_source_notice` / `opt_out_provided`.
- **Dedup vs Odoo** before any upload.
- **`odoo_ready=yes` only after user review.**
- Region posture (Germany strict, EU/UK, US, unknown) — see
  `source-compliance.md`. Tag `region` at search time, not from enrich `country`.
- **Honesty rule** — state plainly in every run summary: EU/DE rows are
  human-review-gated leads, not a send-ready cold list.

## Reusable pipeline scripts (from the DPP pilot)

The pilot was executed with small helper scripts in `outputs/` that are safe to
copy as templates (`_build_people.py`, `_parse_match.py`, `_build_xlsx.py`).
They are run artifacts, not part of the skill — re-implement the same logic
inline or via fresh helpers each run; do not depend on those filenames.