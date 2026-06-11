---
name: find-cold-leads
description: Use when the user wants to find, research, crawl, or export new B2B cold leads, prospect lists, company targets, ICP accounts, public-web contact paths, SerpApi prospecting results, LinkedIn-assisted lead cross-references, or Odoo-ready mailing-list import files. Works for any marketing context — not limited to sustainability or climate.
---

# Find Cold Leads

Find cold leads from public web search signals and export them to Excel for review. Prefer a person-focused workflow: find relevant accounts, then run targeted buyer-role searches for specific individuals and public evidence.

## First Steps

1. Read `.agents/product-marketing-context.md` if it exists.
2. Ask the user to choose a search theme or define a custom one:
   - `generic-b2b` (neutral starting point)
   - `dpp-rollout-sectors`
   - `eu-taxonomy-lca`
   - `standards-triggered-prospects`
   - `linkedin-assisted-cross-reference`
   - custom
3. Ask which search provider to use. Recommend `serper` if the user is unsure.
4. Ask which extraction provider to use. Recommend `codex_builtin` for no-key local extraction or `jina` / `firecrawl` when the user wants an API extractor.

   > **Warning — do not use `codex_builtin` in cloud environments.** `codex_builtin` fetches lead websites directly from the machine running the script. Its private-IP guard re-resolves DNS, so a malicious domain using zero-TTL DNS rebinding can race the check and reach internal services — on AWS/GCP/Azure that includes the instance-metadata endpoint (IAM credentials). On cloud VMs or hosts attached to sensitive internal networks, use `jina`, `firecrawl`, `tavily`, or `exa` instead: they fetch pages from the provider's infrastructure, so no request originates from your machine.
5. Ask for the required API key only if the chosen provider needs one and no matching environment variable is already set. Do not save API keys into files.
6. Ask for geography, max leads, and output filename if not provided.
7. Confirm whether to run `--contact-search` for targeted named-contact discovery after account discovery.
8. Use `scripts/lead_crawler.py` to collect, dedupe, score, enrich contacts, and export leads.

For provider choices and key handling, read `references/providers.md`. For theme details, read `references/search-themes.md`. For LinkedIn, personal data, and outreach boundaries, read `references/source-compliance.md`.

## Discovery Interview (Custom Themes)

When the user wants a non-prebuilt context, run a short interview to build a custom theme JSON. Ask:

1. **Product / service:** What are you selling or what problem do you solve?
2. **Target companies:** What sectors, company types, or sizes are you targeting?
3. **Buyer personas:** What job titles or roles indicate decision-making authority?
4. **Buying signals:** What keywords, triggers, compliance drivers, or events signal intent?
5. **Geography / exclusions:** Where should we search? What should we exclude?
6. **Output:** Max leads and output filename?

Translate the answers into a custom theme JSON (see **Custom Theme JSON** below) and run it.

## Theme Guidance

Recommend `generic-b2b` when the user hasn't specified a niche and wants to experiment.

Recommend `dpp-rollout-sectors` for ClimatePoint's legacy ICP: textiles/apparel, furniture, mattresses, and toys.

Recommend `eu-taxonomy-lca` when the user wants broader LCA-driven opportunities derived from the EU Taxonomy climate delegated-act annexes. This includes manufacturing LCA, energy life-cycle GHG thresholds, digital avoided-emissions LCA, R&D life-cycle performance, and adaptation-annex requirements.

Recommend `standards-triggered-prospects` when the user wants companies already mentioning standards such as ISO 14067, ISO 14064-1, PEFCR, product environmental footprint, or product carbon footprint.

Use `linkedin-assisted-cross-reference` only when the user provides LinkedIn URLs or licensed/manual LinkedIn data. Do not crawl LinkedIn. Store LinkedIn only as a reference and find contact evidence from non-LinkedIn public web sources.

## Qualification (Stage Q)

Qualification is the skill's core job: decide, from **free** signals only (search snippets, the company's own pages, a free name lookup), whether a candidate genuinely fits the target ICP. Never spend enrichment credits to qualify. Assign each candidate one tier:

- **strong** — in a named ICP sector (for ClimatePoint's DPP ICP: a physical-product manufacturer in textiles, apparel, footwear, furniture, mattresses, or toys) **and** carrying an intent signal (ISO 14067, EPD/environmental product declaration, PCF/product carbon footprint, Digital Product Passport, LCA, sustainability report) **and** corroborated to a single resolved official domain.
- **possible** — sector fit but one leg missing: no intent signal yet, an ICP-adjacent product (e.g. packaging), or identity not pinned to one official domain (name-disambiguation guard).
- **reject** — off-ICP (services, finance, SaaS, consultancy, retailer/reseller, competitor selling LCA/EPD tooling), keyword false positives (a "toy" company making digital games), data vendors/directories (even with normal-looking names on non-blocklisted domains), listicle/aggregator titles ("Top 100 … (2026)"), SERP/blog titles captured as a company name, and any contact whose evidence is a third-party data-vendor snippet rather than the company's own pages.

The blocklist is domain-only and will not catch a novel data-vendor domain or a name-token collision (e.g. a real "Apollo" mattress maker) — Stage Q judgment must. Record the tier and the evidence (`evidence_snippet`, `source_url`, `business_relevance_basis`) so the decision is auditable.

## Credit Gate (Apollo enrichment)

When the Apollo MCP is used for people/identity enrichment, treat credits as scarce:

- **Iron rule: never enrich to qualify.** Qualify on free signals (Stage Q) first; spend a credit (`apollo_people_match`) only on rows already tiered `strong` or `possible`.
- Read the credit balance at run start (`apollo_usage_stats_credit_usage_stats`) and enforce a per-run budget (**default 25**). Stop enriching when the budget is exhausted; keep the remaining qualified rows company-level for review.
- Page through free search (`apollo_mixed_people_api_search`); a small `per_page` silently caps results at page 1.
- A no-match `apollo_people_match` costs 0; a matched person costs 1. Record the spend per row so the Run Config total reconciles against the usage delta.

## Script Usage

From the skill folder:

```powershell
python .\scripts\lead_crawler.py --list-themes
python .\scripts\lead_crawler.py --list-providers
```

Run a generic B2B search:

```powershell
python .\scripts\lead_crawler.py --theme generic-b2b --search-provider serper --extract-provider codex_builtin --location "United States" --max-results 50 --output ".\outputs\generic-leads.xlsx"
```

Run a SerpApi-backed search:

```powershell
$env:SERPAPI_KEY = "<key>"
python .\scripts\lead_crawler.py --theme eu-taxonomy-lca --search-provider serpapi --extract-provider codex_builtin --location "Germany" --max-results 50 --output ".\outputs\eu-taxonomy-leads.xlsx"
```

Run a Serper-backed search with a key supplied for only this run:

```powershell
python .\scripts\lead_crawler.py --theme dpp-rollout-sectors --search-provider serper --search-api-key "<key>" --extract-provider jina --location "European Union" --max-results 50 --output ".\outputs\dpp-leads.xlsx"
```

Run interactively and securely prompt for missing keys:

```powershell
python .\scripts\lead_crawler.py --theme standards-triggered-prospects --search-provider tavily --extract-provider firecrawl --prompt-for-keys --location "Germany" --max-results 25 --output ".\outputs\standards-leads.xlsx"
```

Run a person-focused Tavily search:

```powershell
python .\scripts\lead_crawler.py --theme dpp-rollout-sectors --search-provider tavily --search-api-key "<key>" --extract-provider codex_builtin --contact-search --contact-search-queries 6 --location "Germany" --max-results 25 --output ".\outputs\dpp-person-leads.xlsx"
```

Run with manual LinkedIn/company seeds:

```powershell
python .\scripts\lead_crawler.py --theme linkedin-assisted-cross-reference --search-provider codex_manual --manual-seeds ".\seeds.txt" --location "European Union" --output ".\outputs\linkedin-assisted-leads.xlsx"
```

The seeds file is CSV, JSON, or TXT and accepts three kinds of entry:

- **Company website / bare domain** (`acme.de` or `https://acme.de`) — becomes a crawlable lead that is qualified, scored, and enriched like a search hit. A bare domain gains an `https://` scheme automatically.
- **LinkedIn URL** (`https://www.linkedin.com/company/acme`) — stored as `linkedin_reference_url` on a company-level row; never crawled. The company name is taken from the seed's `company` field, or derived from the LinkedIn slug if absent. Find contact evidence on the company's own non-LinkedIn pages.
- **Company name only** — kept as a company-level row awaiting a domain.

JSON/CSV rows may combine fields, e.g. `{"company": "Acme GmbH", "url": "acme.de", "linkedin": "https://www.linkedin.com/company/acme"}`, so a single seed is both crawled and carries its LinkedIn reference. The `linkedin-assisted-cross-reference` theme refuses to run without `--manual-seeds` (it must never crawl LinkedIn).

Run offline with a SerpApi-style fixture:

```powershell
python .\scripts\lead_crawler.py --theme dpp-rollout-sectors --fixture ".\fixture.json" --no-crawl-pages --output ".\outputs\test-leads.xlsx"
```

## Output Review

The Excel workbook contains:

- `Leads`: deduped company leads, target persona, named contact fields when found, contact paths, LinkedIn references, review fields, and Odoo readiness.
- `Sources`: search queries or seed files used.
- `Rejected`: blocked or excluded source URLs such as LinkedIn search results.
- `Run Config`: theme, location, generated timestamp, queries, and guardrails.

Before handing leads to cold email or Odoo work:

1. Review `source_url` and `evidence_snippet`.
2. Confirm `business_relevance_basis`.
3. Check `contact_name`, `contact_title`, `contact_email`, `contact_source_url`, and `contact_page`.
4. Keep `outreach_allowed_review` as `needs review` until the user confirms outreach basis.
5. Mark `odoo_ready=yes` only after review.

## Contact Discovery

Use a person-focused account-to-contact workflow:

1. Discover relevant company domains from the selected search theme.
2. Crawl the company homepage, then follow same-domain public links such as contact, about, team, impressum, sustainability, people, management, and leadership pages.
3. Run targeted contact searches per company with titles defined by the theme's `contact_search_titles`.
4. Prefer contacts matching the theme's buyer personas.
5. Record person-level details only when public-page evidence supports them.
6. If no named person is found, keep the company row and use role-based contact paths where available.

Do not scrape LinkedIn for people. User-provided LinkedIn URLs can be stored as reference signals, then verified against public non-LinkedIn pages.

## Custom Theme JSON

Use a custom theme file when the prebuilt themes are too broad. The JSON can include ICP-specific fields so the crawler knows which buyers and signals to prioritize.

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

Run it with:

```powershell
python .\scripts\lead_crawler.py --custom-theme-file ".\custom-theme.json" --location "Netherlands" --max-results 25 --output ".\outputs\custom-leads.xlsx"
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

- Prefer named buyer contacts when public evidence supports them; otherwise keep company-level leads for review.
- Do not scrape LinkedIn or automate logged-in sites.
- Avoid private, gated, or paid sources unless the user provides exported data.
- Keep source evidence in the workbook.
- Use role-based contact paths where possible.
- Deduplicate by normalized domain.
- Do not claim outreach compliance; prepare leads for human review.
