---
name: find-cold-leads
description: Use when the user wants to find real named B2B cold leads — specific people at specific companies, with verified business emails — and export them to Excel for review. Runs the Apollo people pipeline: free people search by company domain + buyer titles, Stage Q qualification on free signals, then paid bulk enrichment of the top candidates. Yields named contacts, not company rows or role-based catchalls. Works for any marketing context — not limited to sustainability or climate.
---

# Find Cold Leads

Find **real named people** at target companies — verified business email, job
title, seniority, company firmographics — and export them to Excel for review.
The skill runs the **Apollo people pipeline**: a free people search by company
domain + buyer titles, Stage Q qualification on free signals, then paid bulk
enrichment of the top candidates. Output is named contacts, not company rows or
role-based catchalls (`info@`, `sustainability@`).

The pipeline discovers people *at known companies*. It does not discover
companies. Bring a list of company domains (from prior account discovery, a seed
file, or a user-provided list), plus a theme that defines which sectors count as
ICP fit and which titles count as buyers.

## First Steps

1. Read `.agents/product-marketing-context.md` if it exists.
2. **Confirm the Apollo MCP is connected.** This skill is Apollo-driven; without
   it there is no pipeline. Apollo authenticates via OAuth — do not store any API
   key in a file.
3. **Get the company domains.** Ask the user for a domain list, a seed file, or
   the output of a prior account-discovery run. If none exists, stop and run
   account discovery first (out of scope for this skill).
4. Ask the user to choose a search theme or define a custom one:
   - `generic-b2b` (neutral starting point)
   - `dpp-rollout-sectors`
   - `eu-taxonomy-lca`
   - `standards-triggered-prospects`
   - custom (run the Discovery Interview)
5. Ask for geography (region filter), the credit budget (**default 25**), max
   contacts to enrich, and the output filename if not provided.
6. Read the credit balance at run start (`apollo_usage_stats_credit_usage_stats`)
   and confirm the budget is available. Surface the `mcp_credits` block to the
   user whenever Apollo returns one.
7. Run the pipeline in `references/apollo-people-pipeline.md`.

For theme details, read `references/search-themes.md`. For compliance,
personal-data, and outreach boundaries, read `references/source-compliance.md`.
For the exact Apollo parameter recipe, ranking, capping, and export schema, read
`references/apollo-people-pipeline.md`. For handing results to the
climatepoint-contact-intelligence classifier, read `references/handoff-schema.md`.

## Discovery Interview (Custom Themes)

When the user wants a non-prebuilt context, run a short interview to build a
custom theme JSON. Ask:

1. **Product / service:** What are you selling or what problem do you solve?
2. **Target companies:** What sectors, company types, or sizes are you targeting?
3. **Buyer personas:** What job titles or roles indicate decision-making
   authority? (These become Apollo `person_titles`.)
4. **Buying signals:** What keywords, triggers, compliance drivers, or events
   signal intent? (Used by Stage Q, not by Apollo search.)
5. **Geography / exclusions:** Where should we search? What should we exclude?
6. **Output:** Max contacts, credit budget, and output filename?

Translate the answers into a custom theme JSON (see **Custom Theme JSON**
below) and run it.

## Theme Guidance

Themes define three things the pipeline needs: **sectors** (Stage Q ICP fit),
**`contact_search_titles`** (→ Apollo `person_titles`), and **`lead_signals`**
(intent evidence for Stage Q). They no longer drive a web crawler.

Recommend `generic-b2b` when the user hasn't specified a niche and wants to
experiment.

Recommend `dpp-rollout-sectors` for ClimatePoint's legacy ICP: textiles/apparel,
footwear, furniture, mattresses, and toys.

Recommend `eu-taxonomy-lca` when the user wants broader LCA-driven opportunities
derived from the EU Taxonomy climate delegated-act annexes (manufacturing LCA,
energy life-cycle GHG, digital avoided-emissions LCA, R&D, adaptation).

Recommend `standards-triggered-prospects` when the user wants companies already
mentioning standards such as ISO 14067, ISO 14064-1, PEFCR, product environmental
footprint, or product carbon footprint (these surface as `lead_signals` for
Stage Q).

Use `linkedin-assisted-cross-reference` only when the user provides LinkedIn URLs
or licensed/manual LinkedIn data. Do not crawl LinkedIn. Store LinkedIn only as a
reference.

## Qualification (Stage Q)

Qualification is the skill's core job: decide, from **free** signals only (the
Phase 1 Apollo search result — org industry, title, resolved domain), whether a
candidate genuinely fits the target ICP. **Never spend enrichment credits to
qualify.** Assign each candidate one tier:

- **strong** — in a named ICP sector (for ClimatePoint's DPP ICP: a
  physical-product manufacturer in textiles, apparel, footwear, furniture,
  mattresses, or toys) **and** a buyer title matching the theme's
  `buyer_title_terms` **and** corroborated to a single resolved official domain.
- **possible** — sector or title fit but one leg missing: no intent signal yet,
  an ICP-adjacent product (e.g. packaging), or identity not pinned to one
  official domain.
- **reject** — off-ICP (services, finance, SaaS, consultancy, retailer/reseller,
  competitor selling LCA/EPD tooling), keyword false positives, data
  vendors/directories, listicle/aggregator titles, SERP/blog titles captured as a
  person or company name.

Record the tier and the evidence (`evidence_snippet`, `source_url`,
`business_relevance_basis`) so the decision is auditable. Only `strong` /
`possible` proceed to paid enrichment.

## Apollo People Pipeline

The primary workflow. Full recipe in `references/apollo-people-pipeline.md`.

1. **Phase 1 — free people search (0 credits):** `apollo_mixed_people_api_search`
   with `q_organization_domains_list` (batch ~20 domains/call), `person_titles`
   (theme `contact_search_titles`), `include_similar_titles=true`, `per_page=100`.
   Page through while `has_more`. Collect `id`, `first_name`, `last_name` (may be
   masked — expected), `title`, `has_email` flag, `organization.name`. No emails
   or phones yet.
2. **Rank and cap (free):** seniority score (Chief/CSO=6, VP/Head=5, Director=4,
   Sr Mgr=3, Mgr/Lead=2, other=1). Per company: `has_email=true` first, then
   seniority desc. **Cap 3/company.** Global sort: `has_email` + seniority.
3. **Stage Q qualify** on the free signals (above). Only `strong`/`possible`
   proceed.
4. **Phase 2 — paid bulk enrichment:** `apollo_people_bulk_match` on the top N
   (≤ budget). Max **10 per request**; `details` = `{id, first_name,
   organization_name}`. `reveal_personal_emails=false`,
   `run_waterfall_email=false`, `run_waterfall_phone=false`,
   `reveal_phone_number=false`. Returns verified business email, `email_status`,
   unmasked name, `linkedin_url`, org firmographics, `seniority`. 1 credit/match,
   0 no-match.
5. **Reconcile credits** vs the post-run `apollo_usage_stats` delta. Surface the
   `mcp_credits` block.
6. **Dedup vs Odoo** `mailing.contact` before export.
7. **Export** the Excel workbook (Leads, Remaining Candidates, Sources, Run
   Config, Credit Ledger).

## Credit Gate (Apollo enrichment)

Treat credits as scarce. Iron rules:

- **Never enrich to qualify.** Qualify on free signals (Stage Q) first; spend a
  credit only on rows already tiered `strong` or `possible`.
- Read the credit balance at run start (`apollo_usage_stats_credit_usage_stats`)
  and enforce a per-run budget (**default 25**). Stop enriching when the budget
  is exhausted; keep the remaining qualified `has_email=true` rows in the
  Remaining Candidates sheet for a future cycle.
- **Free search is free** — `apollo_mixed_people_api_search` costs 0 credits
  regardless of result count. Page through it fully (`per_page=100`); a small
  `per_page` silently caps results at page 1.
- **Bulk enrichment** — `apollo_people_bulk_match`: 1 credit per matched person,
  0 per no-match, max 10 per request. (Single `apollo_people_match` is the same
  unit cost for one person; use bulk for ≥2.)
- **No waterfall, no phone reveal** — `run_waterfall_email/phone=false`,
  `reveal_phone_number=false`. Waterfall credit cost is variable and
  plan-dependent; do not enable it unless the user explicitly opts in and you
  have confirmed the team's waterfall capability first.
- Record the spend per row so the Run Config total reconciles against the usage
  delta. **Surface the `mcp_credits` block to the user** whenever present —
  estimated cost before a spend, actual spend + new balance after.

## Output Review

The Excel workbook contains:

- `Leads`: one row per enriched named contact — `contact_name`, `first_name`,
  `last_name`, `job_title`, `business_email`, `email_status`,
  `linkedin_reference_url`, company firmographics, `seniority`, Apollo IDs,
  source fields, and compliance fields (`outreach_allowed_review`,
  `gdpr_legitimate_interest_basis`, `art14_source_notice`, `opt_out_provided`,
  `personal_email_used`, `waterfall_used`, `odoo_ready`, `odoo_dupcheck`).
- `Remaining Candidates`: `has_email=true` people not enriched (budget cap),
  company-level, for the next cycle.
- `Sources`: Phase 1 free-search batches + Phase 2 enrichment batches, with
  credits and result counts.
- `Run Config`: domains searched, ICP, buyer persona, search titles, people
  found free, people enriched, emails verified, credits budget/spent/remaining,
  compliance basis.
- `Credit Ledger`: per-credit-type limit/consumed/remaining + run total.

Before handing leads to cold email or Odoo work:

1. Review `business_email` and `email_status` (prefer `verified`).
2. Confirm `business_relevance_basis` and the Stage Q tier.
3. Check `seniority` and `job_title` match the buyer persona.
4. Keep `outreach_allowed_review` as `needs review` until the user confirms
   outreach basis.
5. Confirm `odoo_dupcheck` = no duplicates.
6. Mark `odoo_ready=yes` only after user review.

## Custom Theme JSON

Use a custom theme file when the prebuilt themes are too broad. The JSON feeds
the pipeline: `sectors` → Stage Q ICP fit, `contact_search_titles` → Apollo
`person_titles`, `lead_signals` / `buyer_title_terms` → Stage Q intent + title
matching.

```json
{
  "id": "custom-packaging-pcf",
  "label": "Packaging PCF prospects",
  "sectors": ["packaging manufacturer", "plastic packaging supplier"],
  "keywords": ["product carbon footprint", "ISO 14067", "LCA"],
  "subthemes": ["Packaging", "Plastics", "Supplier requests"],
  "target_personas": "Sustainability / Procurement / Quality Manager",
  "contact_search_titles": [
    "Head of Sustainability",
    "Procurement Manager",
    "Quality Manager",
    "Sustainability Director",
    "Supply Chain Manager"
  ],
  "buyer_title_terms": [
    "sustainability",
    "procurement",
    "quality",
    "supply chain",
    "compliance"
  ],
  "lead_signals": [
    "product carbon footprint",
    "ISO 14067",
    "LCA",
    "sustainability report",
    "packaging"
  ],
  "high_priority_title_terms": ["sustainability", "procurement", "compliance"],
  "medium_priority_title_terms": ["quality", "supply chain"]
}
```

### Generic custom theme example (non-sustainability)

```json
{
  "id": "cybersecurity-fintech",
  "label": "Cybersecurity for FinTech",
  "sectors": ["fintech", "payment processor", "digital banking", "neobank"],
  "keywords": ["cybersecurity", "SOC 2", "penetration testing", "data breach", "compliance"],
  "subthemes": ["FinTech", "Cybersecurity", "Compliance"],
  "target_personas": "CISO / CTO / VP Engineering / Head of Security / Compliance Officer",
  "contact_search_titles": [
    "Chief Information Security Officer",
    "CTO",
    "VP Engineering",
    "Head of Security",
    "Compliance Officer",
    "Security Manager",
    "Director of Engineering"
  ],
  "buyer_title_terms": [
    "security",
    "compliance",
    "engineering",
    "technology",
    "chief",
    "director",
    "vp",
    "head"
  ],
  "lead_signals": [
    "cybersecurity",
    "SOC 2",
    "penetration testing",
    "data breach",
    "compliance",
    "fintech security"
  ],
  "high_priority_title_terms": ["security", "ciso", "compliance"],
  "medium_priority_title_terms": ["engineering", "technology", "director"]
}
```

## Quality Bar

- Deliver **named buyer contacts with verified business emails** — not company
  rows and not role-based catchalls. If a company has no buyer-title person in
  Apollo, leave it out (no fallback to `info@`).
- Business email only (`reveal_personal_emails=false`); waterfall off; no phone
  reveal.
- Do not scrape LinkedIn or automate logged-in sites. Store `linkedin_url` as a
  reference only.
- Cap 3 candidates per company; rank by seniority, `has_email=true` first.
- Never enrich to qualify — Stage Q on free signals first.
- Deduplicate by normalized email; dedup vs Odoo `mailing.contact` before upload.
- Keep source evidence and the Stage Q tier in the workbook.
- Do not claim outreach compliance; prepare leads for human review.
  `outreach_allowed_review` stays `needs review`; `odoo_ready=yes` only after the
  user approves. State plainly: EU/DE rows are human-review-gated leads, not a
  send-ready cold list.