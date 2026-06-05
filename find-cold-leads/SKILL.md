---
name: find-cold-leads
description: >
  Find and qualify B2B cold leads, then hand them to the ClimatePoint contact
  classifier. Use whenever the user wants to find new leads, prospect lists,
  ICP/target accounts, DPP-sector or LCA prospects, Apollo lead pulls, public-web
  contact paths, or an Odoo-ready / classifier-ready lead export. The skill's job
  is QUALIFICATION: it decides which companies genuinely fit ClimatePoint's ICP,
  attaches evidence + region-aware compliance posture, and spends scarce Apollo
  enrichment credits only on qualified leads. Prefer this skill for any "find me
  companies / people who need our services" request, even when the user does not
  name Apollo or a specific theme.
---

# Find Cold Leads

This skill turns a vague "find companies who might need us" into a short list of
**ICP-qualified leads with evidence and a clean contact path**, ready to hand to
`climatepoint-contact-intelligence` for scoring.

The hard part is not *finding* names — Apollo and the open web return thousands.
The hard part is **judgment**: most search results are directories, blogs, data
vendors, or off-ICP companies. The previous version of this skill failed exactly
here (it stored blog titles as company names and kept zoominfo.com as a "lead").
So the center of this skill is a qualification protocol you apply with care, and a
**credit discipline** that spends Apollo's limited enrichment budget only on
companies you have actually qualified.

## What this skill owns (and what it does not)

| This skill (`find-cold-leads`) | The classifier (`climatepoint-contact-intelligence`) |
|---|---|
| Discover candidate companies/people | Persona classification |
| **Qualify** ICP fit + intent evidence | Lead score (1–10) |
| Find contact path + LinkedIn reference | Need state, opportunity, outreach angle |
| Region-aware compliance posture | Next action |
| Export a classifier-ready file | Reads that file and adds the scoring columns |

**Do not compute persona or lead-score here.** Produce qualified rows; the
classifier adds the scoring. The output schema is built to be the classifier's
input (see `references/handoff-schema.md`).

## First steps

1. Read `.agents/product-marketing-context.md` if it exists — it defines the ICP,
   buyer personas, and intent signals you qualify against. If absent, use the ICP
   summary in the "ICP definition" section below.
2. Confirm the run parameters with the user (don't assume): **geography/region**,
   **target sector(s)**, **max leads**, **credit budget** (default 25), and
   **output filename**.
3. Decide the **mode** (next section). When unsure, recommend **Mode A (Apollo)**
   if the Apollo MCP is connected, falling back to **Mode O (open web)**.

## Modes

- **Mode A — Apollo MCP (primary).** Best people+identity data, proven EU coverage.
  Free search is unlimited; enrichment (the email/LinkedIn unlock) costs **1 credit
  per matched person** and the balance is small, so qualification must precede
  enrichment.
- **Mode O — open web (fallback / net-new).** No credits. Lower yield, role-based
  contact paths, and finds net-new companies Apollo's database misses. Use when
  Apollo is unavailable, credits are exhausted, the user passes `--no-enrich`, or
  the user wants companies outside Apollo.
- **Mode P — generic provider CSV (deferred).** Not built yet. If the user has an
  Airscale/Apollo CSV export, see `references/providers.md` for the planned ingest
  shape; for now, treat it as manual seeds.

**Run-start branch (decide before searching):**
- Apollo MCP tools **not connected** → go straight to Mode O; record
  `source_mode=open_web`.
- Apollo connected but **0 credits** or `--no-enrich` → Mode A free search for
  identity (no email) + Mode O role paths; rows are `contact_source=apollo_free`.
- Apollo connected with credits → full Mode A.

## The pipeline

```
search (free) → STAGE Q: qualify on FREE signals → CREDIT GATE → STAGE C: contact
+ compliance → shared writer → Excel + classifier-ready CSV → classifier
```

You drive Stages Q and C with judgment. `scripts/lead_crawler.py` does the
deterministic plumbing for Mode O (search, blocklist, dedup, fetch) and is the
**single writer** for the output of both modes (`--write-leads`). Never hand-build
the Excel/CSV; feed your qualified rows to the writer so the schema stays correct.

---

## Stage Q — qualify on FREE signals (the heart)

**Iron rule: never enrich to qualify.** Enrichment is the *reward* for passing Q,
not an input to it. The fields that cost a credit — `email`,
`organization.industry`, `organization.primary_domain`, `country` — are **not**
available here and must **not** be required to make the qualify decision. If you
find yourself wanting to enrich "just to check fit," stop: that is the exact
behavior that burns the budget on junk.

What you *do* have for free:
- **Apollo free search** returns `id`, `first_name`, masked last name, `title`,
  `organization.name`, and a `has_email` flag (plus presence flags like
  `has_industry` — flags, not values).
- **A free web lookup keyed on `organization.name`** (WebSearch) — this is where
  the intent signal comes from.
- **Mode O**: the company's own fetched pages (homepage/about/sustainability).

### Step 1 — Is it a real operating company?
Reject directories, blogs, listicles, news, and data vendors. The script's
blocklist catches known vendor domains (zoominfo, lusha, apollo, ensun, …) by
registrable domain, but you are the backstop for the subtle cases: a "Top 100
textile companies 2026" listicle, a trade-press article, an aggregator. If the
candidate is not a single operating company that makes or sells something, it goes
to **Rejected**.

### Step 2 — Name-disambiguation guard
Pre-enrichment you only have a company **name**, and names are ambiguous — this is
the source of the old "Storchenwiege blog title" failure. Before accepting an
intent-signal web hit as belonging to this company, require a **corroborating
signal**: the name must resolve to a single official company domain, and the intent
evidence must come from *that* domain (or an authoritative registry / association /
exhibitor list that names the company). A name-only match with no resolved official
domain **caps the row at `possible`** — never `strong`, and never auto-enriched.

Important nuance: "couldn't resolve a clean domain" is a reason to *downgrade to
`possible` for human review*, **not** to reject. Only reject when the candidate is
positively not a prospect (a directory/blog/vendor, or clearly off-ICP). A
plausible-sector company with a generic/ambiguous name is a `possible` lead you
keep — discarding it loses real leads, and the only cost of `possible` is that it
gets enriched last. Reserve `reject` for things you're confident are *not*
companies-that-could-buy.

### Step 3 — ICP fit (two independent conjuncts)
A lead is ICP-fit only if **both** hold:

1. **Sector / product** — a physical product in a DPP sector: textiles / apparel /
   footwear, furniture, mattresses, toys (or a clearly DPP-adjacent physical
   manufacturer). Judge from the free web lookup + the Apollo industry keyword
   prior. (`organization.industry` from enrichment is **not** used here.)
2. **Compliance pressure** — at least **one intent signal**: EPD, ISO 14067,
   PEFCR, PCF / "product carbon footprint", a published sustainability report,
   Digital Product Passport, or a supplier PCF/LCA request.

### Step 4 — Assign a tier
- **`strong`** — both conjuncts confirmed, official domain resolved, ≥1 intent
  signal found on that domain.
- **`possible`** — sector confirmed but identity/intent only weakly corroborated,
  OR a clean sector fit with no intent signal yet. Both are kept as leads, but
  only the *intent-bearing* `possible` rows are enrich-eligible (see Credit gate
  step 3) — a no-intent sector-fit isn't worth a scarce credit.
- **`weak`** — neither conjunct solid → **Rejected**.

Record per qualified row: `qualification_tier`, `intent_signal`,
`business_relevance_basis`, `evidence_snippet`, `source_url`. `weak` and non-fit
companies go to the **Rejected** sheet with a reason — keep them, don't silently
drop, so the user can audit what was excluded.

> `qualification_tier` is *not* the classifier's lead-score. It only decides who
> deserves an enrichment credit.

---

## Credit gate (Mode A only)

The whole point of the skill is to spend the small Apollo budget well. Enforce it
**continuously**, not as a single up-front check:

1. Read the live balance: `apollo_usage_stats_credit_usage_stats`.
2. `effective_budget = min(user_budget, balance_at_start)`. Tell the user if this
   clamps below what they asked for.
3. Enrich **`strong` rows first, then `possible` rows that carry ≥1 intent
   signal** (i.e. `possible` only for weak *identity* corroboration), each having
   `has_email`. A clean sector-fit with **no** intent signal stays a `possible`
   lead in the export but is **not** enrich-eligible: it fails conjunct 2 of the
   ICP test (Step 3), and enrichment returns email/firmographics — never the
   missing intent signal — so a credit could never rescue its fit. Spending one
   would violate the two-conjunct gate.
4. Keep a local `credits_spent` counter, incremented **only on a matched**
   `apollo_people_match` (a no-match returns 0 credits — don't count it). Stop
   strictly while `credits_spent < effective_budget` (with balance 294 the 295th
   attempt is never made). This live counter is what gates overspend, so it counts
   on **match** (conservative: a matched-but-no-email reveal still consumed the
   lookup). It is deliberately **not** the same as the per-row
   `apollo_credits_consumed` field the writer records — that field counts a credit
   only on a *revealed* enrich, so the Run Config total never phantom-charges
   free-search or budget-exhausted `apollo_free` rows. Two counters, two jobs.
5. If Apollo returns an insufficient-credit / quota error, **hard-stop cleanly**:
   finalize the run and mark the remaining qualified rows `apollo_free`.
6. For large batches, re-read the balance once mid-run. Note in Run Config that a
   shared account can be spent concurrently — an accepted limitation, not a
   guarantee.

Enrichment is keyed on the free-search **`id`** (carry it as `apollo_person_id`).
`apollo_people_match` returns the plaintext `email`, `email_status`, unmasked
`last_name`, `linkedin_url`, and the full `organization` firmographics.

---

## Stage C — contact path + region-aware compliance wall

**Having a verified email is not permission to send it.** This wall is the
difference between "quality leads" and a complaint. Region is decided at **search
time** from the location filter the user gave (Mode A `person_locations` / Mode O
query region) — *not* from the enrichment `country` field, because most rows are
never enriched and would otherwise all collapse to the conservative default.
Enrichment `country` only *refines* the search-time region (override if it clearly
contradicts).

- **EU/UK rows:** set `consent_status=unknown`, `outreach_allowed_review=needs
  review`, populate `legitimate_interest_basis`, flag country (Germany = strict,
  consent-leaning even B2B; France/UK professional addresses more permissive).
  Prefer the role-based path; store any personal email but gate outreach to human
  review.
- **US rows:** opt-out (CAN-SPAM) posture — named-email outreach is defensible
  *with* a working unsubscribe + accurate sender identity.
- **Region unknown:** conservative EU posture.

Contact path by `contact_source`:
- `apollo_enriched` — verified `email` (use the **email's** domain for outreach,
  which can differ from `organization.primary_domain`), `linkedin_reference_url`.
- `apollo_free` / `open_web` — no verified personal email; use a role-based path
  (`sustainability@`, `info@`, contact form) found on the company's **own** pages,
  plus a public-web LinkedIn reference if discoverable. For `apollo_free`, resolve
  the company domain from the open web (free search withholds it).

Full legal reasoning and the per-country table are in
`references/source-compliance.md`. Read it before a run targeting the EU.

**Honesty rule (state it in your summary):** EU/DE rows are *human-review-gated
leads*, not a send-ready cold list. Don't let "quality German leads" be mistaken
for "cold-email-ready German leads."

---

## Output + handoff

Two artifacts, both written by `scripts/lead_crawler.py --write-leads`:

1. **Excel workbook** — `Leads`, `Sources`, `Rejected`, `Run Config`. Run Config
   records region(s), mode/branch, credit budget + `effective_budget` + credits
   consumed, pages fetched vs `total_entries`, and counts
   (searched / blocked / qualified / enriched / dropped) so truncation is visible.
2. **Classifier-ready CSV** — exactly the columns
   `climatepoint-contact-intelligence` consumes, so its `set_if_empty` preserves
   what you pre-fill and skips re-researching Apollo rows.

The Apollo→column mapping and the two-domains rule (email domain for outreach,
`primary_domain` for identity/dedup/blocklist) are in
`references/handoff-schema.md`. Follow it exactly — a wrong column name silently
breaks the handoff.

## Apollo MCP call sequence (Mode A)

```
apollo_usage_stats_credit_usage_stats        # balance -> effective_budget
apollo_mixed_people_api_search (PAGED)        # loop `page` until enough / total_entries
  -> Stage Q on each candidate (free signals) # no enrich here
  -> credit gate                              # strong then possible, within budget
apollo_people_match (per selected id)         # 1 credit/match; carries apollo_person_id
  -> Stage C mapping + compliance
-> lead_crawler.py --write-leads              # shared writer
```

Page through results — a small `per_page` against `total_entries` of thousands
silently caps at page 1 otherwise. Record pages fetched in Run Config.

## ICP definition (fallback if context doc absent)

ClimatePoint sells LCA / Digital-Product-Passport compliance software. ICP:
physical-product manufacturers in DPP sectors (**textiles/apparel/footwear,
furniture, mattresses, toys**; EU DPP deadlines 2027–2030) and adjacent
manufacturers under EU compliance pressure. Buyer personas: Head of Sustainability,
ESG Manager, Sustainability / Product-Compliance / Quality / Supply-Chain /
Procurement Manager, LCA Specialist. Intent signals: EPD, ISO 14067, PEFCR, PCF,
product carbon footprint, sustainability report, Digital Product Passport.

## Script usage

```powershell
# list themes / providers
python .\scripts\lead_crawler.py --list-themes
python .\scripts\lead_crawler.py --list-providers

# Mode O discovery (open web), writes Excel + CSV
python .\scripts\lead_crawler.py --mode open_web --theme dpp-rollout-sectors `
  --location "Germany" --max-results 25 --output ".\outputs\dpp-de.xlsx"

# Write leads you assembled (Mode A / mixed): pass a candidates JSON
python .\scripts\lead_crawler.py --write-leads --candidates ".\outputs\candidates.json" `
  --output ".\outputs\dpp-de.xlsx"

# offline self-test (no network, no credits)
python .\scripts\lead_crawler.py --mode open_web --fixture ".\references\sample-serpapi-fixture.json" `
  --no-crawl-pages --output ".\outputs\test.xlsx"
```

See `references/search-themes.md` for intent-signal query templates and seed lists,
`references/providers.md` for search/Apollo/provider details, and
`references/source-compliance.md` for the compliance wall.

## Quality bar

- Qualify on free signals; spend a credit only on a row you'd defend as ICP-fit.
- Every lead carries `evidence_snippet` + `source_url`; every contact's evidence is
  the company's **own** domain, never a data-vendor snippet.
- Reject directories/blogs/vendors/off-ICP to the Rejected sheet — visibly.
- Never scrape LinkedIn; store LinkedIn URLs only as references.
- Never present EU rows as send-ready; the skill prepares review-gated leads.
- Deduplicate by registrable domain (eTLD+1).
